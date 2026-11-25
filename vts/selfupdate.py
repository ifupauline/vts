# SPDX-License-Identifier: Apache-2.0
"""
vts keeping itself and Streamlink up to date, without anyone doing it.

Once a day, vts asks GitHub for the latest version of its repository and
compares it with the one installed (pip notes which one it installed:
direct_url.json; or vts's own note, data/vts-commit). A newer one is
installed after vts quits, by a helper that waits for it to end (Windows
can't replace a running program) and then runs pip in vts's own
environment. Once a week the same helper updates Streamlink. The next
start says what happened (data/self-update.json).

Only an installed vts updates itself: not one run from its source, nor
one in a place this user can't write to.
"""

import json
import os
import shutil
import subprocess
import sys
import time

from vts import http, log, platform

REPO = "ifupauline/vts"
LATEST_URL = f"https://api.github.com/repos/{REPO}/commits/main"
GIT_SOURCE = f"git+https://github.com/{REPO}@{{sha}}"
ZIP_SOURCE = f"https://github.com/{REPO}/archive/{{sha}}.zip"
VTS_EVERY = 24 * 3600
STREAMLINK_EVERY = 7 * 24 * 3600
PIP_TIMEOUT = 900
WAIT_MAX = 600  # for the program to quit


def _sha(s):
    return isinstance(s, str) and len(s) == 40 and all(
        c in "0123456789abcdef" for c in s)


def _data(name):
    d = platform.data_dir()
    return os.path.join(d, name) if d else None


def installed():
    """
    (the commit of the vts installed, or None; whether it may update
    itself): an installed copy in a place this user can write to
    """
    import importlib.metadata as md
    try:
        dist = md.distribution("vts")
    except md.PackageNotFoundError:
        return None, False
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        where = os.path.abspath(str(dist.locate_file("vts")))
    except (TypeError, ValueError):
        where = None
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        direct = {}
    # only a copy pip installed from GitHub (pipx install git+https://...):
    # not one run from its source (a developer's), nor installed to edit
    url = str(direct.get("url", "")).rstrip("/")
    if (direct.get("dir_info") or {}).get("editable") or where != here or \
            not url.startswith(f"https://github.com/{REPO}"):
        return None, False
    if not os.access(here, os.W_OK):
        return None, False
    commit = (direct.get("vcs_info") or {}).get("commit_id")
    try:
        note = platform.read_file(_data("vts-commit"), 64).strip()
    except (OSError, ValueError, TypeError):
        note = ""
    # installed from an archive (no git): the helper's own note
    if not _sha(commit) and _sha(note):
        commit = note
    return (commit if _sha(commit) else None), True


def latest():
    """the latest commit of vts on GitHub, or None"""
    url = os.environ.get("VTS_SELF_URL") or LATEST_URL
    r = http.fetch(http.Request(url, headers={
        "Accept": "application/vnd.github.sha"}, max_size=4096,
        timeout=15.0, retries=1))
    sha = r.body.decode("ascii", "replace").strip() if r.status == 200 \
        else ""
    if not _sha(sha):
        log.dbg(f"vts update: no version from GitHub ({r.status} "
                f"{r.message})")
        return None
    return sha


def check():
    """
    once a day: the newer version of vts to install when it quits (kept in
    data/vts-pending), or None
    """
    from vts import update
    if not update.due("vts", VTS_EVERY):
        return pending()
    update.done("vts")
    commit, ok = installed()
    if not ok:
        return None
    new = latest()
    if not new or new == commit:
        return None
    try:
        platform.write_atomic(_data("vts-pending"), new + "\n")
    except (OSError, TypeError):
        return None
    return new


def pending():
    try:
        sha = platform.read_file(_data("vts-pending"), 64).strip()
    except (OSError, ValueError, TypeError):
        return None
    return sha if _sha(sha) else None


def streamlink_due():
    from vts import update
    return update.due("streamlink", STREAMLINK_EVERY)


def after_exit(force=False):
    """
    when vts quits: the helper that installs what is pending (and updates
    Streamlink weekly), started now, waiting for this process to end;
    whether one was started. @force: Streamlink now too (vts update)
    """
    from vts import update
    sha = pending()
    sl = force or streamlink_due()
    if not sha and not sl:
        return False
    _, ok = installed()
    if not ok:
        return False
    if sl:
        update.done("streamlink")
    py = sys.executable
    if platform.WINDOWS:
        # no window for it
        w = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(w):
            py = w
    argv = [py, "-m", "vts.selfupdate", "--after", str(os.getpid())]
    if sha:
        argv += ["--to", sha]
    if sl:
        argv.append("--streamlink")
    try:
        platform.spawn(argv, quiet=True, detach=True)
    except OSError as e:
        log.dbg(f"vts update: {e}")
        return False
    return True


def report():
    """what the helper did after the last quit, for the first message, or
    None (said once)"""
    path = _data("self-update.json")
    try:
        r = json.loads(platform.read_file(path, 65536))
        os.unlink(path)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(r, dict):
        return None
    said = []
    if r.get("vts") == "ok":
        said.append(f"vts was updated ({str(r.get('sha', ''))[:7]})")
    elif r.get("vts"):
        said.append(f"vts couldn't update itself: {r['vts']}")
    if r.get("streamlink") not in (None, "ok"):
        said.append(f"Streamlink couldn't update: {r['streamlink']}")
    return "; ".join(said) or None


# --- the helper, run after vts quits ----------------------------------------

def _wait(pid):
    end = time.monotonic() + WAIT_MAX
    if platform.WINDOWS:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if h:
            k32.WaitForSingleObject(h, WAIT_MAX * 1000)
            k32.CloseHandle(h)
        return
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except OSError:
            return
        time.sleep(0.5)


def _pip(*args, upgrade=True):
    """pip in vts's own environment: None if it went well, else why"""
    # the tests' own pip
    pip = os.environ.get("VTS_PIP", "")
    if pip and os.path.isabs(pip):
        pip = [sys.executable, pip] if pip.endswith(".py") else [pip]
    else:
        pip = [sys.executable, "-m", "pip"]
    try:
        p = subprocess.run(pip + ["install", "--quiet",
                                  "--disable-pip-version-check",
                                  *(["--upgrade"] if upgrade else []),
                                  *args],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=PIP_TIMEOUT,
                           **platform._popen_flags(False))
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)[:200]
    if p.returncode == 0:
        return None
    lines = [ln for ln in p.stdout.decode("utf-8", "replace").splitlines()
             if ln.strip()]
    return (lines[-1] if lines else f"pip ended with {p.returncode}")[:300]


def requirements():
    """what the vts now installed needs, asked of a new Python (this one
    still has the old vts in mind)"""
    if os.environ.get("VTS_PIP"):
        return []
    code = ("import importlib.metadata as m\n"
            "for r in m.requires('vts') or []:\n"
            "    'extra ==' in r or print(r)\n")
    try:
        p = subprocess.run([sys.executable, "-c", code],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=60,
                           **platform._popen_flags(False))
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in p.stdout.decode("utf-8", "replace")
            .splitlines() if ln.strip()]


def helper(argv):
    """python -m vts.selfupdate --after PID [--to SHA] [--streamlink]"""
    pid = int(argv[argv.index("--after") + 1]) if "--after" in argv else 0
    sha = argv[argv.index("--to") + 1] if "--to" in argv else None
    if pid:
        _wait(pid)
    out = {"when": int(time.time())}
    if sha and _sha(sha):
        source = (GIT_SOURCE if shutil.which("git") else ZIP_SOURCE).format(
            sha=sha)
        # pip keeps a version it has when the number is the same, whatever
        # the commit: vts alone, forced, then what the new one needs
        why = _pip("--force-reinstall", "--no-deps", source, upgrade=False)
        if not why:
            needs = requirements()
            if needs:
                why = _pip(*needs, upgrade=False)
        out["sha"] = sha
        out["vts"] = why or "ok"
        if not why:
            platform.write_atomic(_data("vts-commit"), sha + "\n")
            try:
                os.unlink(_data("vts-pending"))
            except OSError:
                pass
    if "--streamlink" in argv:
        out["streamlink"] = _pip("streamlink") or "ok"
    path = _data("self-update.json")
    if path:
        platform.write_atomic(path, json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    if sys.stdout is None or sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    sys.exit(helper(sys.argv[1:]))

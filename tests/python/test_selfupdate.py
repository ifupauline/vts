# SPDX-License-Identifier: Apache-2.0
"""Everything kept up to date without the user: Twitch's and SOOP's
descriptions from the repository, vts itself and Streamlink after quitting
(here with a fake pip, tests/fake_pip.py)"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

import pytest

from conftest import ROOT, TESTS
from vts import selfupdate, sites, update

FAKE_PIP = os.path.join(TESTS, "fake_pip.py")
OLD = "1" * 40
NEW = "abcdef0123456789abcdef0123456789abcdef01"


def post(url, data):
    urllib.request.urlopen(urllib.request.Request(
        url, method="POST", data=data), timeout=10).read()


@pytest.fixture
def home(tmp_path, monkeypatch, youtube):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("VTS_BASE_URL", youtube.url)
    monkeypatch.setenv("VTS_SELF_URL", youtube.url + "/latest-commit")
    monkeypatch.setenv("VTS_PIP", FAKE_PIP)
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "fake.log"))
    monkeypatch.delenv("FAKE_PIP_FAIL", raising=False)
    os.makedirs(selfupdate.platform.data_dir(), exist_ok=True)
    yield tmp_path
    for name in ("/twitch.json", "/soop.json", "/latest-commit"):
        post(youtube.url + name, b"")
    sites.forget("twitch")
    sites.forget("soop")


def pips(home):
    log = home / "fake.log"
    if not log.exists():
        return []
    with open(log) as f:
        return [e["pip"] for e in map(json.loads, f) if "pip" in e]


# --- Twitch's and SOOP's descriptions --------------------------------------

def test_site_description_updated(home, youtube):
    from vts.sites.twitch import load_description
    built = load_description()
    newer = dict(built, revision=built["revision"] + 1,
                 client_id="newer-client-id")
    post(youtube.url + "/twitch.json", json.dumps(newer).encode())
    got = update.update_sites()
    assert got["twitch"] == built["revision"] + 1
    # nothing newer for SOOP
    assert got["soop"] is None
    assert update.best_site("twitch")["client_id"] == "newer-client-id"
    assert update.best_site("soop")["revision"] >= 1
    # the Twitch made from now on uses it
    assert sites.get("twitch").d["client_id"] == "newer-client-id"
    # the same again: not newer
    assert update.update_sites()["twitch"] is None


def test_site_description_older_or_bad(home, youtube):
    from vts.sites.soop import load_description
    built = load_description()
    post(youtube.url + "/soop.json",
         json.dumps(dict(built, revision=built["revision"] - 1)).encode())
    assert update.update_sites()["soop"] is None
    post(youtube.url + "/soop.json", b'{"revision": 9999999999}')
    why = update.update_sites()["soop"]
    assert isinstance(why, str) and "soop.json" in why
    # the one built in still used, nothing left behind
    assert update.best_site("soop") == built
    cache = os.listdir(home / "cache" / "vts")
    assert not [n for n in cache if n.startswith("soop.json")], cache


def test_site_description_kept_but_older_than_built(home):
    from vts.sites.twitch import load_description
    built = load_description()
    path = update.site_cache_path("twitch")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(dict(built, revision=1, client_id="old"), f)
    # a vts updated since: its own is newer than the one kept
    assert update.best_site("twitch") == built


# --- vts itself ----------------------------------------------------------

def test_not_installed_from_source():
    # the tests run vts from its source: it doesn't update itself
    assert selfupdate.installed() == (None, False)


class FakeDist:
    def __init__(self, where, direct):
        self.where, self.direct = where, direct

    def locate_file(self, name):
        return os.path.join(self.where, name)

    def read_text(self, name):
        return json.dumps(self.direct) if name == "direct_url.json" else None


def test_installed_commit(home, monkeypatch):
    import importlib.metadata as md
    dist = FakeDist(ROOT, {"url": "https://github.com/ifupauline/vts",
                           "vcs_info": {"vcs": "git", "commit_id": OLD}})
    monkeypatch.setattr(md, "distribution", lambda name: dist)
    assert selfupdate.installed() == (OLD, True)
    # installed from an archive: pip notes no commit, the helper did
    with open(os.path.join(selfupdate.platform.data_dir(), "vts-commit"),
              "w") as f:
        f.write(NEW + "\n")
    assert selfupdate.installed() == (OLD, True)
    dist.direct = {"url": f"https://github.com/ifupauline/vts/archive/"
                          f"{NEW}.zip", "archive_info": {}}
    assert selfupdate.installed() == (NEW, True)
    # from somewhere else: left alone
    dist.direct = {"url": "https://example.com/vts.zip", "archive_info": {}}
    assert selfupdate.installed() == (None, False)
    # installed to be edited (a developer's): left alone
    dist.direct = {"url": "file:///src", "dir_info": {"editable": True}}
    assert selfupdate.installed() == (None, False)


def test_check_notes_a_newer_version(home, youtube, monkeypatch):
    monkeypatch.setattr(selfupdate, "installed", lambda: (OLD, True))
    assert selfupdate.check() is None  # GitHub said nothing
    assert selfupdate.pending() is None
    post(youtube.url + "/latest-commit", NEW.encode())
    # once a day
    assert selfupdate.check() is None
    update.done("vts")
    with open(update._stamp_path("vts"), "w") as f:
        f.write("1\n")
    assert selfupdate.check() == NEW
    assert selfupdate.pending() == NEW
    # the same as the one installed: nothing to do
    monkeypatch.setattr(selfupdate, "installed", lambda: (NEW, True))
    os.unlink(os.path.join(selfupdate.platform.data_dir(), "vts-pending"))
    with open(update._stamp_path("vts"), "w") as f:
        f.write("1\n")
    assert selfupdate.check() is None
    assert selfupdate.pending() is None


def test_after_exit_starts_the_helper(home, monkeypatch):
    started = []
    monkeypatch.setattr(selfupdate.platform, "spawn",
                        lambda argv, **kw: started.append(argv))
    monkeypatch.setattr(selfupdate, "installed", lambda: (OLD, False))
    assert not selfupdate.after_exit()  # not to be updated
    monkeypatch.setattr(selfupdate, "installed", lambda: (OLD, True))
    # the first time: Streamlink is due, nothing pending for vts
    assert selfupdate.after_exit()
    argv = started.pop()
    assert argv[1:5] == ["-m", "vts.selfupdate", "--after",
                         str(os.getpid())]
    assert "--streamlink" in argv and "--to" not in argv
    # a week before Streamlink again; nothing to do
    assert not selfupdate.after_exit()
    with open(os.path.join(selfupdate.platform.data_dir(), "vts-pending"),
              "w") as f:
        f.write(NEW + "\n")
    assert selfupdate.after_exit()
    argv = started.pop()
    assert argv[-2:] == ["--to", NEW] and "--streamlink" not in argv
    # vts update: Streamlink too, now
    assert selfupdate.after_exit(force=True)
    assert "--streamlink" in started.pop()


def test_helper_installs_and_reports(home):
    with open(os.path.join(selfupdate.platform.data_dir(), "vts-pending"),
              "w") as f:
        f.write(NEW + "\n")
    assert selfupdate.helper(["--to", NEW, "--streamlink"]) == 0
    first, sl = pips(home)
    # vts itself, forced (pip keeps a version of the same number), then
    # Streamlink
    assert first[:2] == ["install", "--quiet"]
    assert "--force-reinstall" in first and "--no-deps" in first
    assert first[-1].endswith(NEW) and "ifupauline/vts" in first[-1]
    assert sl[-2:] == ["--upgrade", "streamlink"]
    data = selfupdate.platform.data_dir()
    assert not os.path.exists(os.path.join(data, "vts-pending"))
    with open(os.path.join(data, "vts-commit")) as f:
        assert f.read().strip() == NEW
    assert selfupdate.report() == "vts was updated (abcdef0)"
    assert selfupdate.report() is None  # said once


def test_helper_failing(home, monkeypatch):
    monkeypatch.setenv("FAKE_PIP_FAIL", "ifupauline/vts")
    with open(os.path.join(selfupdate.platform.data_dir(), "vts-pending"),
              "w") as f:
        f.write(NEW + "\n")
    selfupdate.helper(["--to", NEW, "--streamlink"])
    # tried again next time
    assert selfupdate.pending() == NEW
    assert selfupdate.report() == \
        "vts couldn't update itself: ERROR: no network"
    monkeypatch.setenv("FAKE_PIP_FAIL", "streamlink")
    selfupdate.helper(["--streamlink"])
    assert selfupdate.report() == \
        "Streamlink couldn't update: ERROR: no network"


def test_helper_waits_for_vts_to_quit(home):
    # "vts": a program quitting in a second
    vts = subprocess.Popen([sys.executable, "-c",
                            "import time; time.sleep(1)"])
    threading.Thread(target=vts.wait, daemon=True).start()
    env = dict(os.environ, PYTHONPATH=ROOT)
    start = time.monotonic()
    subprocess.run([sys.executable, "-m", "vts.selfupdate", "--after",
                    str(vts.pid), "--streamlink"], env=env, timeout=60,
                   check=True)
    assert time.monotonic() - start >= 0.8
    assert vts.poll() is not None
    assert pips(home) == [["install", "--quiet", "--disable-pip-version-check",
                           "--upgrade", "streamlink"]]
    assert selfupdate.report() is None  # Streamlink updated: nothing to say

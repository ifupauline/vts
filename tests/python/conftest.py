# SPDX-License-Identifier: Apache-2.0
"""What the tests share: the fake YouTube, a home of their own, vts itself."""

import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))))
TESTS = os.path.join(ROOT, "tests")
FIXTURES = os.path.join(TESTS, "fixtures")
FAKE_MPV = os.path.join(TESTS, "fake_mpv.py")
WINDOWS = os.name == "nt"

sys.path.insert(0, ROOT)


class FakeYouTube:
    """fake_server.py with the recorded YouTube answers"""

    def __init__(self, work):
        portfile = os.path.join(work, "port")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(TESTS, "fake_server.py"), portfile,
             os.path.join(FIXTURES, "youtube")])
        for _ in range(200):
            if os.path.exists(portfile) and os.path.getsize(portfile):
                break
            time.sleep(0.05)
        with open(portfile) as f:
            self.url = "http://127.0.0.1:" + f.read().strip()

    def get(self, path):
        import urllib.request
        with urllib.request.urlopen(self.url + path, timeout=10) as r:
            return r.read().decode()

    def last_request(self):
        import json
        return json.loads(self.get("/last-request"))

    def stop(self):
        self.proc.terminate()
        self.proc.wait()


@pytest.fixture(scope="session")
def youtube(tmp_path_factory):
    yt = FakeYouTube(str(tmp_path_factory.mktemp("youtube")))
    yield yt
    yt.stop()


@pytest.fixture
def env(tmp_path, youtube):
    """an environment of its own: home, folders, the fake YouTube"""
    home = tmp_path / "home"
    home.mkdir()
    e = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "en_US.UTF-8",
        "VTS_BASE_URL": youtube.url,
        "VTS_IMAGE_URL": youtube.url,
        "PYTHONPATH": ROOT,
        "PYTHONIOENCODING": "utf-8",
    }
    if WINDOWS:
        for var in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            if var in os.environ:
                e[var] = os.environ[var]
        e["USERPROFILE"] = str(home)
        e["APPDATA"] = str(home / "AppData" / "Roaming")
        e["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    else:
        e["XDG_CONFIG_HOME"] = str(home / ".config")
        e["XDG_DATA_HOME"] = str(home / ".local" / "share")
        e["XDG_CACHE_HOME"] = str(home / ".cache")
        e["XDG_RUNTIME_DIR"] = str(tmp_path / "run")
    return e


class Result:
    def __init__(self, p):
        self.code = p.returncode
        self.out = p.stdout
        self.err = p.stderr

    def __repr__(self):
        return f"exit {self.code}, out {self.out!r}, err {self.err!r}"


@pytest.fixture
def vts(env):
    """run vts with @args in the test's environment"""
    def run(*args, extra=None, stdin=None, timeout=30):
        e = dict(env, **(extra or {}))
        p = subprocess.run([sys.executable, "-m", "vts", *args],
                           capture_output=True, text=True, encoding="utf-8",
                           env=e, timeout=timeout, input=stdin)
        return Result(p)
    return run


def config_dir(env):
    if WINDOWS:
        return os.path.join(env["APPDATA"], "vts")
    return os.path.join(env["XDG_CONFIG_HOME"], "vts")


def cache_dir(env):
    if WINDOWS:
        return os.path.join(env["LOCALAPPDATA"], "vts", "cache")
    return os.path.join(env["XDG_CACHE_HOME"], "vts")

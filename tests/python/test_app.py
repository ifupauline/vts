# SPDX-License-Identifier: Apache-2.0
"""
What the app does beyond the old checks: the Settings screen, the
recent searches, the playback keys, what was watched, the Downloads,
the buttons of notifications. On a pseudo terminal, read with pyte.
"""

import json
import os
import stat
import sys
import time

import pytest
from conftest import FAKE_MPV, ROOT, TESTS, WINDOWS

pytestmark = pytest.mark.skipif(WINDOWS, reason="needs a pseudo terminal")


@pytest.fixture
def app(tmp_path, env):
    pytest.importorskip("pyte")
    sys.path.insert(0, TESTS)
    import test_cli
    prog = tmp_path / "vts"
    prog.write_text(f"#!/bin/sh\nPYTHONPATH={ROOT} exec {sys.executable} "
                    "-m vts \"$@\"\n")
    prog.chmod(prog.stat().st_mode | stat.S_IXUSR)
    conf = tmp_path / "vts.conf"
    conf.write_text(f"player = {FAKE_MPV}\ndownloader = {FAKE_MPV}\n"
                    "thumbnails = no\nupdates = no\n")
    log = tmp_path / "fake.log"
    e = dict(env, FAKE_PLAN="[[30, 600]]", FAKE_WAIT="1", FAKE_LOG=str(log))
    started = []

    def start(cols=100, rows=30, prog=str(prog)):
        t = test_cli.Tui(prog, ["-c", str(conf)], e, cols, rows)
        started.append(t)
        assert t.pump("Home", 10), t.text()
        return t
    start.conf = conf
    start.log = log
    yield start
    for t in started:
        t.close()


def commands(log):
    if not log.exists():
        return []
    with open(log) as f:
        return [e["command"] for e in map(json.loads, f) if "command" in e]


def test_settings_screen(app):
    t = app()
    assert t.keys("4", "Saved in"), t.text()
    assert "› Settings" in t.line(0)
    # in sections: General first, then each site's
    assert t.wait(lambda: "player = " in t.text() and
                  "The program that plays videos" in t.text()), t.text()
    for section in ("General", "YouTube"):
        assert any(t.line(y).strip() == section for y in range(30)), \
            t.text()
    # quality, the 3rd of YouTube's: from a menu, written in the file
    t.keys("jjjjjjjjj", "quality = 1080")
    assert t.wait(lambda: "highest quality" in t.text())
    t.keys("\r", "best")
    assert t.keys("gj\r", "quality = 2160"), t.text()
    with open(app.conf) as f:
        assert "quality = 2160\n" in f.read()
    # back to the default with d
    t.keys("d", "quality = 1080 (the default)")
    with open(app.conf) as f:
        assert "quality" not in f.read()
    # a Twitch setting, kept under its name
    t.keys("jjjj", "twitch.quality = best")
    assert t.wait(lambda: "Twitch's live streams" in t.text()), t.text()
    t.keys("\r", "1080")
    assert t.keys("jj\r", "twitch.quality = 720"), t.text()
    with open(app.conf) as f:
        assert "twitch.quality = 720\n" in f.read()
    # the check, as vts doctor does it
    t.keys("Gk\r", "Check")
    assert t.wait(lambda: "ok  search" in t.text(), 15), t.text()


def test_recent_searches(app):
    t = app()
    t.keys("/")
    t.keys("linus torvalds\r", "Search: linus torvalds")
    t.keys("1", "Home")
    t.pump(None, 0.3)
    t.keys("/", "Esc or Backspace")
    # nothing typed yet: the recent ones
    assert t.wait(lambda: "↺ linus torvalds" in t.text()), t.text()
    assert t.keys("\t\r", "Search: linus torvalds"), t.text()


def test_search_links(app):
    # a @handle or a link opens what it names, not a search for its words
    t = app()
    t.keys("/")
    assert t.keys("@kurzgesagt\r", "Kurzgesagt", 10), t.text()
    assert "Search:" not in t.line(0), t.line(0)
    # (on a channel, / searches in it: from Home)
    t.keys("1", "Home")
    t.keys("/")
    t.keys("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42\r", None, 0.5)
    assert t.wait(lambda: "Never Gonna" in t.text()), t.text()
    t.keys("1", "Home")
    t.keys("/")
    t.keys("@nobodyhere\r", None, 0.5)
    assert t.wait(lambda: "no channel @nobodyhere" in t.text()), t.text()
    # words still search
    t.keys("/")
    assert t.keys("linus torvalds\r", "Search: linus torvalds"), t.text()


def test_search_as_handle(app):
    # nothing found for the words: the channel whose handle they are
    t = app()
    t.keys("/")
    assert t.keys("kurzgesagt\r", "Search: kurzgesagt"), t.text()
    assert t.wait(lambda: "@kurzgesagt" in t.text()), t.text()
    t.keys("1", "Home")
    t.keys("/")
    t.keys("nothing at all\r", "Search: nothing at all")
    assert t.wait(lambda: "Nothing found for nothing at all." in t.text()), \
        t.text()


def test_wide_letters_by_hand(app, tmp_path):
    # as on Windows: curses draws blanks, vts writes the letters into them
    prog = tmp_path / "vts-wide"
    prog.write_text(f"#!/bin/sh\nPYTHONPATH={ROOT} exec {sys.executable} -c "
                    "'import sys; import vts.tui.draw as D; "
                    "D.WIDE_BY_HAND = True; from vts.main import main; "
                    "sys.exit(main())' \"$@\"\n")
    prog.chmod(prog.stat().st_mode | stat.S_IXUSR)
    t = app(prog=str(prog))
    t.keys("/", "Search")
    t.keys("스타 채널 abc", None, 0.5)
    assert t.wait(lambda: t.line(29).startswith(
        "Search: 스타 채널 abc")), repr(t.line(29))
    # gone with the prompt, not left behind
    t.keys("\x1b", None, 0.5)
    assert t.wait(lambda: "스타" not in t.text()), t.text()


def test_live_badge(app):
    # live streams: a white on red LIVE before the title
    t = app()
    t.keys("/")
    t.keys("lofi radio\r", "Search: lofi radio")
    assert t.wait(lambda: "watching" in t.text()), t.text()
    rows = [y for y in range(30) if t.line(y).startswith(("▌ LIVE ",
                                                           "  LIVE "))]
    assert rows, t.text()
    cell = t.screen.buffer[rows[0]][2]
    assert (cell.data, cell.fg, cell.bg) == ("L", "white", "red"), cell
    # said once: not again in the second line
    assert not t.line(rows[0] + 1).lstrip().startswith("LIVE"), t.text()


def test_playback_keys(app):
    t = app()
    t.keys("/")
    t.keys("linus torvalds\r", "Building the PERFECT")
    assert t.keys("p", "0:30/10:00", 10), t.line(0)
    for k in ".,90m ":
        t.keys(k, timeout=0.3)
    t.keys("j", timeout=0.3)
    assert t.keys("A", "Added to what plays"), t.text()
    t.pump(None, 0.5)
    got = commands(app.log)
    for cmd in (["seek", "10", "relative"], ["seek", "-10", "relative"],
                ["add", "volume", "-5"], ["add", "volume", "5"],
                ["cycle", "mute"], ["cycle", "pause"]):
        assert cmd in got, got
    assert any(c[0] == "loadfile" and c[2] == "append" for c in got), got
    t.keys("x", "Stopped")
    # what was watched: marked in the lists
    t.keys("g", None, 0.3)
    assert t.wait(lambda: "stopped at 0:30" in t.line(2)), t.line(2)


def formats(log):
    """what each run of mpv was asked for: format and order"""
    with open(log) as f:
        return [" ".join(a for a in e["argv"]
                         if a.startswith(("--ytdl-format=",
                                          "--ytdl-raw-options-append=format")))
                for e in map(json.loads, f) if "argv" in e]


def test_sound_quality(app):
    # a plays the sound in the quality of the settings
    with open(app.conf, "a") as f:
        f.write("sound_quality = medium\n")
    t = app()
    t.keys("/")
    t.keys("linus torvalds\r", "Building the PERFECT")
    assert t.keys("a", "Sound only, medium quality"), t.text()
    t.keys("x", "Stopped")
    # Q: the sound only, then which sound quality
    t.keys("Q", "Play in which quality?")
    t.keys("G\r", "Sound quality?")
    # it starts on the setting (medium)
    assert t.keys("j\r", "Sound only, low quality"), t.text()
    t.keys("x", "Stopped")
    assert formats(app.log)[-2:] == [
        "--ytdl-format=bestaudio/best "
        "--ytdl-raw-options-append=format-sort=abr~128",
        "--ytdl-format=bestaudio/best "
        "--ytdl-raw-options-append=format-sort=abr~64"], formats(app.log)
    # Esc in the second menu plays nothing
    n = len(formats(app.log))
    t.keys("Q", "Play in which quality?")
    t.keys("G\r", "Sound quality?")
    t.keys("\x1b", None, 0.5)
    assert len(formats(app.log)) == n


def test_favourites_add_and_export(app, env):
    t = app()
    t.keys("2", "Favourites")
    t.keys("N", "Add the channel")
    assert t.keys("@kurzgesagt\r", "is a favourite"), t.text()
    assert t.wait(lambda: "Kurzgesagt" in t.text()), t.text()
    # into the Downloads folder by default
    t.keys("e", "Export to")
    assert t.keys("\r", "Favourites written to"), t.text()
    path = os.path.join(env["HOME"], "Downloads", "vts-favourites.opml")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "<opml" in text and "UCsXVk37bltHxD1rDPwtNM8Q" in text
    # as CSV when the name says so (Ctrl-U clears the line)
    csv = os.path.join(env["HOME"], "favs.csv")
    t.keys("e", "Export to")
    t.keys("\x15" + csv + "\r", None, 0.3)
    assert t.wait(lambda: os.path.exists(csv)), t.text()
    with open(csv, encoding="utf-8") as f:
        assert f.read().startswith("Channel Id,Channel Url,Channel Title\n")


def test_update_now(app, env):
    import urllib.request
    from vts import rules
    builtin = json.loads(rules.builtin_text())
    rev = builtin["revision"]
    urllib.request.urlopen(urllib.request.Request(
        env["VTS_BASE_URL"] + "/rules.json", method="POST",
        data=json.dumps(dict(builtin, revision=rev + 1)).encode()),
        timeout=10).read()
    t = app()
    t.keys("4", "Saved in")
    t.keys("G", "Update now")
    assert t.keys("\r", f"YouTube's pages: revision {rev + 1}", 20), t.text()
    assert "Updated yt-dlp to stable@2026.11.11" in t.text(), t.text()


def test_downloads_screen(app):
    t = app()
    t.keys("/")
    t.keys("linus torvalds\r", "Building the PERFECT")
    t.keys("d", "Download")
    t.keys("\r", "Downloading")
    assert t.pump("✓ Downloaded:", 15), t.text()
    t.keys("3", "Library")
    assert t.pump("what you watched"), t.text()
    assert "Downloads" in t.text()
    t.keys("Gk\r", "Downloaded")
    assert t.pump("x.mkv"), t.text()
    t.keys("-", "Remove it from the list?")
    t.keys("j\r", None, 0.5)
    assert t.wait(lambda: "x.mkv" not in t.text()), t.text()


def test_notification_buttons(tmp_path, vts, env):
    """the Watch later button of a notification: the video kept"""
    notifier = tmp_path / "notify-send"
    notifier.write_text("#!/bin/sh\n"
                        "[ \"$1\" = --version ] && { echo notify-send "
                        "0.8.3; exit 0; }\n"
                        f"echo \"$@\" >> {tmp_path}/notes\n"
                        "case \"$*\" in *--wait*) echo later;; esac\n")
    notifier.chmod(0o755)
    r = vts("notified", str(notifier), "mfv0V1SxbNA", "Linus Tech Tips",
            "Building")
    assert r.code == 0, r
    assert "--action=later=Watch later" in (tmp_path / "notes").read_text()
    assert vts("later").out.startswith("video\tmfv0V1SxbNA\tBuilding the "
                                       "PERFECT")
    # from vts check: the notification with its buttons, in the background
    from vts import notify
    os.environ["PATH"] = str(tmp_path) + os.pathsep + os.environ["PATH"]
    try:
        assert notify._has_actions(str(notifier))
    finally:
        os.environ["PATH"] = os.environ["PATH"].split(os.pathsep, 1)[1]
    time.sleep(0)

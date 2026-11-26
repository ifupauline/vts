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


def down_to(t, about):
    """down the Settings to the one explained so"""
    for _ in range(60):
        if about in t.text():
            return
        os.write(t.fd, b"j")
        t.pump(None, 0.1)
    assert t.wait(lambda: about in t.text()), t.text()


def test_settings_screen(app):
    t = app()
    assert t.keys("4", "Saved in"), t.text()
    assert "› Settings" in t.line(0)
    # in sections: General first, then each site's
    assert t.wait(lambda: "player = " in t.text() and
                  "The program that plays videos" in t.text()), t.text()
    for section in ("General", "YouTube"):
        assert t.wait(lambda: any(t.line(y).strip() == section
                                  for y in range(30))), t.text()
    # quality: from a menu, written in the file
    down_to(t, "The highest quality videos play in")
    t.keys("\r", "best")
    assert t.keys("gj\r", "quality = 2160"), t.text()
    with open(app.conf) as f:
        assert "quality = 2160\n" in f.read()
    # back to the default with d
    t.keys("d", "quality = 1080 (the default)")
    with open(app.conf) as f:
        assert "quality" not in f.read()
    # a Twitch setting, kept under its name
    down_to(t, "The quality Twitch's live streams play in")
    t.keys("\r", "1080")
    assert t.keys("jj\r", "twitch.quality = 720"), t.text()
    with open(app.conf) as f:
        assert "twitch.quality = 720\n" in f.read()
    # the check, as vts doctor does it
    t.keys("Gk\r", "Check")
    assert t.wait(lambda: "ok  search" in t.text(), 15), t.text()


def test_sites(app):
    t = app()
    assert " vts  YouTube  Twitch  SOOP  › Home" in t.line(0), t.line(0)
    bar = t.screen.buffer[0]
    youtube_bg = bar[7].bg
    # YouTube stands out (reversed), the others don't
    assert bar[7].reverse and not bar[16].reverse, t.line(0)
    # where it was is kept: Library on YouTube
    t.keys("3", "Library")
    assert t.keys("b", "No Twitch favourites yet."), t.text()
    assert "› Home" in t.line(0)
    # Twitch's colours
    bar = t.screen.buffer[0]
    twitch_bg = bar[0].bg
    assert bar[16].reverse and twitch_bg != youtube_bg, (bar[0], youtube_bg)
    assert t.keys("b", "No SOOP favourites yet."), t.text()
    assert t.screen.buffer[0][0].bg not in (youtube_bg, twitch_bg)
    t.keys("b", None, 0.3)
    assert t.wait(lambda: "YouTube  Twitch  SOOP  › Library" in t.line(0)), \
        t.line(0)
    assert t.screen.buffer[0][0].bg == youtube_bg


def test_alt_digits(app):
    # Alt+1, 2, 3: a site straight away (terminals send Esc then the digit)
    t = app()
    t.keys("3", "Library")
    assert t.keys("\x1b3", "No SOOP favourites yet."), t.text()
    assert t.keys("\x1b2", "No Twitch favourites yet."), t.text()
    t.keys("\x1b1", None, 0.3)
    assert t.wait(lambda: "YouTube  Twitch  SOOP  › Library" in t.line(0)), \
        t.line(0)
    # Esc alone still goes back
    t.keys("2", "Favourites")
    t.keys("/", "Search")
    t.keys("\x1b", None, 0.3)
    # (in the search box, 3 would be typed)
    assert t.keys("3", "› Library"), t.text()
    assert t.keys("\x1b3", "No SOOP favourites yet."), t.text()


def test_site_favourites(app):
    # 2 on Twitch and SOOP: their favourite channels, as on YouTube
    t = app()
    t.keys("b", "No Twitch favourites yet.")
    t.keys("2", None, 0.2)
    assert t.wait(lambda: "› Favourites" in t.line(0) and "N adds" in
                  t.text()), t.text()
    # N adds one by its name, or by a link
    t.keys("N", "Add the Twitch channel")
    assert t.keys("esl_sc2\r", "ESL_SC2 is a favourite"), t.text()
    t.keys("N", "Add the Twitch channel")
    assert t.keys("https://www.twitch.tv/comebacktv\r",
                  "ComeBackTV is a favourite"), t.text()
    assert t.wait(lambda: "ComeBackTV" in t.text() and "ESL_SC2" in
                  t.text() and "Favourites" in t.line(1)), t.text()
    # the same again: said
    t.keys("N", "Add the Twitch channel")
    assert t.keys("esl_sc2\r", "ESL_SC2 is already a favourite"), t.text()
    # a group (on ComeBackTV, the first): it goes under it, last
    t.keys("g", "Group")
    t.keys("StarCraft\r", None, 0.3)
    assert t.wait(lambda: "StarCraft" in t.text() and
                  t.text().index("ESL_SC2") < t.text().index("ComeBackTV")), \
        t.text()
    # Enter: the channel's page
    t.keys("G\r", None, 0.5)
    assert t.wait(lambda: "comebacktv · 27,541 followers" in t.text()), \
        t.text()
    # - removes (after asking)
    t.keys("2", "› Favourites")
    t.keys("G-", "Remove")
    t.keys("j\r", None, 0.5)
    assert t.wait(lambda: "ComeBackTV" not in t.text()), t.text()
    assert "ESL_SC2" in t.text()
    # SOOP: a name that isn't exact shows what it finds
    t.keys("\x1b3", "No SOOP favourites yet.")
    t.keys("2", None, 0.2)
    assert t.wait(lambda: "› Favourites" in t.line(0) and "N adds" in
                  t.text()), t.text()
    t.keys("N", "Add the SOOP channel")
    assert t.keys("김정우\r", "f keeps one of these"), t.text()
    assert t.wait(lambda: "effort김정우" in t.line(1)), t.text()
    t.keys("2", None, 0.2)
    assert t.wait(lambda: "› Favourites" in t.line(0) and "N adds" in
                  t.text()), t.text()
    # by its id
    t.keys("N", "Add the SOOP channel")
    assert t.keys("momo130\r", "is a favourite"), t.text()
    assert t.wait(lambda: "effort김정우" in t.text() and "momo130" in
                  t.text()), t.text()


def test_dislikes(app):
    # asked while the page shows, when the setting says so
    t = app()
    t.keys("/")
    t.keys("https://www.youtube.com/watch?v=dQw4w9WgXcQ\r", None, 0.5)
    assert t.wait(lambda: "Never Gonna" in t.text()), t.text()
    t.pump(None, 1.5)
    assert "dislikes" not in t.text()
    t.keys("q", None, 0.5)
    with open(app.conf, "a") as f:
        f.write("dislikes = yes\n")
    t = app()
    t.keys("/")
    t.keys("https://www.youtube.com/watch?v=dQw4w9WgXcQ\r", None, 0.5)
    assert t.wait(lambda: " likes · 412K dislikes" in t.text()), t.text()


def test_start_site(app):
    with open(app.conf, "a") as f:
        f.write("start_site = last\n")
    t = app()
    t.keys("bb", "No SOOP favourites yet.")
    t.keys("q", None, 0.5)
    t = app()
    assert t.wait(lambda: "No SOOP favourites yet." in t.text()), t.text()
    with open(app.conf, "a") as f:
        f.write("start_site = twitch\n")
    t = app()
    assert t.wait(lambda: "No Twitch favourites yet." in t.text()), t.text()


def streamlink_runs(log):
    if not log.exists():
        return []
    with open(log) as f:
        return [e["streamlink"] for e in map(json.loads, f)
                if "streamlink" in e]


def test_twitch(app):
    t = app()
    t.keys("b", "No Twitch favourites yet.")
    # finding a channel by its name: the one named so first, live
    t.keys("/", "Find a Twitch channel")
    assert t.keys("esl_sc2\r", "Find: esl_sc2"), t.text()
    assert t.wait(lambda: t.line(1).startswith("▌ LIVE  ESL_SC2") and
                  "StarCraft II" in t.line(2) and "watching" in t.line(2)), \
        t.text()
    assert t.keys("f", "ESL_SC2 is a favourite, on Twitch's Home"), t.text()
    # a link opens its channel, from any site
    t.keys("b", "No SOOP favourites yet.")
    t.keys("/")
    t.keys("https://www.twitch.tv/comebacktv\r", None, 0.5)
    assert t.wait(lambda: "comebacktv · 27,541 followers" in t.text()), \
        t.text()
    assert "Twitch" in t.line(0) and "ComeBackTV" in t.line(0), t.line(0)
    t.keys("f", "ComeBackTV is a favourite")
    # Home: live ones first, the others greyed
    t.keys("1", "Live now")
    assert t.wait(lambda: "Offline" in t.text() and "619 watching" in
                  t.text() and "· offline" in t.text()), t.text()
    lines = t.text().split("\n")
    live = next(i for i, ln in enumerate(lines) if "Live now" in ln)
    off = next(i for i, ln in enumerate(lines) if ln.strip() == "Offline")
    assert "LIVE" in lines[live + 1] and "619 watching" in lines[live + 2]
    assert "ESL_SC2" in lines[off + 1] and "offline" in lines[off + 2]
    assert t.screen.buffer[off + 1][3].fg != t.screen.buffer[live + 1][9].fg \
        or not t.screen.buffer[off + 1][3].bold
    # p plays it through Streamlink, in twitch.quality (best)
    t.keys("p", "Playing live, best")
    assert t.wait(lambda: streamlink_runs(app.log)), t.text()
    run = streamlink_runs(app.log)[-1]
    assert run[-2:] == ["https://www.twitch.tv/comebacktv", "best"], run
    assert t.wait(lambda: "/live " in t.line(0)), t.line(0)
    t.keys("x", "Stopped")
    # Q: another quality
    t.keys("Q", "Play in which quality?")
    t.keys("jj\r", "Playing live, 720p")
    assert t.wait(lambda: streamlink_runs(app.log)[-1][-1].startswith(
        "720p60,720p,720p30,480p60")), streamlink_runs(app.log)[-1]
    t.keys("x", "Stopped")
    # d records it
    t.keys("d", "Record in which quality?")
    t.keys("\r", "Recording")
    assert t.wait(lambda: "● rec" in t.line(0)), t.line(0)
    assert t.wait(lambda: "--output" in streamlink_runs(app.log)[-1])
    assert streamlink_runs(app.log)[-1][-2] == \
        "https://www.twitch.tv/comebacktv"
    # - removes a favourite (after asking)
    t.keys("G-", "Remove ESL_SC2 from favourites?")
    assert t.keys("j\r", "Removed ESL_SC2"), t.text()
    assert t.wait(lambda: "Offline" not in t.text()), t.text()


def test_soop(app):
    t = app()
    t.keys("bb", "No SOOP favourites yet.")
    t.keys("/", "Find a SOOP channel")
    assert t.keys("김정우\r", "Find: 김정우"), t.text()
    assert t.wait(lambda: "effort김정우" in t.line(1)), t.text()
    # its page: the live stream, then its past broadcasts
    t.keys("\r", "Past broadcasts")
    assert t.wait(lambda: "momo130 · 121,652 fans" in t.text()), t.text()
    assert "Live now" in t.text() and "LIVE" in t.text()
    assert t.keys("f", "effort김정우 is a favourite, on SOOP's Home")
    # down to the end: the next page comes
    assert t.text().count(" views · ") == 3, t.text()
    t.keys("G", None, 0.5)
    assert t.wait(lambda: t.text().count(" views · ") == 6), t.text()
    # a past broadcast plays with yt-dlp, from SOOP's page, in
    # soop.vod_quality (best)
    t.keys("gjj\r", "Playing")
    assert t.wait(lambda: mpv_runs(app.log)), t.text()
    run = mpv_runs(app.log)[-1]
    assert run[-1].startswith("https://vod.sooplive.com/player/"), run
    assert "--ytdl-format=bestvideo+bestaudio/best" in run, run
    t.keys("x", "Stopped")
    # stopped in the middle: on SOOP's Home
    t.keys("1", "Continue watching")
    assert t.wait(lambda: "stopped at 0:30" in t.text()), t.text()
    # a stream that needs an account says so
    t.keys("/")
    t.keys("https://play.sooplive.co.kr/lockedbj\r", "Locked")
    assert t.wait(lambda: "19+" in t.text()), t.text()
    t.keys("\r", None, 0.3)
    assert t.wait(lambda: "needs a SOOP account" in t.text()), t.text()


def mpv_runs(log):
    if not log.exists():
        return []
    with open(log) as f:
        return [e["argv"] for e in map(json.loads, f) if "argv" in e]


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


def test_channel_posts(app, env):
    # Tab goes through a channel's tabs, posts and podcasts included
    t = app()
    t.keys("/")
    assert t.keys("@kurzgesagt\r", "Kurzgesagt", 10), t.text()
    assert t.wait(lambda: "Videos  Shorts  Live  Playlists  Podcasts  Posts"
                  in t.text()), t.text()
    t.keys("[", None, 0.3)
    # posts: their text in full, likes and comments over it
    assert t.wait(lambda: "likes · " in t.text() and " comments" in
                  t.text()), t.text()
    t.keys("[", None, 0.3)
    assert t.wait(lambda: "This channel has no podcasts." in t.text()), \
        t.text()
    # the tabs Tab goes through: a setting
    t.keys("4", "Saved in")
    for _ in range(30):
        if "channel_tabs = " in t.text() and "▌channel_tabs" in t.text():
            break
        os.write(t.fd, b"j")
        t.pump(None, 0.1)
    assert t.wait(lambda: "posts (what it writes" in t.text()), t.text()
    t.keys("\r", "channel_tabs (a space")
    t.keys("\x15videos,posts\r", None, 0.5)
    assert t.wait(lambda: "channel_tabs = videos,posts" in t.text()), \
        t.text()
    t.keys("1/", None, 0.3)
    assert t.keys("@kurzgesagt\r", "Kurzgesagt", 10), t.text()
    assert t.wait(lambda: "Videos  Posts" in t.text() and "Shorts" not in
                  t.line(4)), t.text()


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


def test_said_after_updating_itself(app, env):
    # what the helper did after the last quit: said at the next start, once
    data = os.path.join(env["LOCALAPPDATA"], "vts") if WINDOWS else \
        os.path.join(env["XDG_DATA_HOME"], "vts")
    os.makedirs(data, exist_ok=True)
    with open(os.path.join(data, "self-update.json"), "w") as f:
        json.dump({"sha": "abcdef0123456789abcdef0123456789abcdef01",
                   "vts": "ok", "streamlink": "ok"}, f)
    t = app()
    assert t.wait(lambda: "vts was updated (abcdef0)" in t.text()), t.text()
    assert not os.path.exists(os.path.join(data, "self-update.json"))


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


def test_download_all(app):
    # D on a playlist: all of it, in a folder of its own, what was
    # downloaded before skipped
    t = app()
    t.keys("/")
    t.keys("https://www.youtube.com/playlist?list="
           "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs\r", None, 0.5)
    assert t.wait(lambda: "› The Existential Crisis" in t.line(0)), t.text()
    t.keys("D", "Download all of")
    t.keys("\r", "Downloading all of")
    # the top bar: which video of how many
    assert t.wait(lambda: "3/3 " in t.line(0) or "1/3 " in t.line(0), 10), \
        t.line(0)
    assert t.pump("(1 of 3 couldn't be: Private video)", 15), t.text()
    with open(app.log) as f:
        dl = [json.loads(ln)["argv"] for ln in f if '"argv"' in ln and
              "--yes-playlist" in ln][0]
    assert dl[-1] == ("https://www.youtube.com/playlist?list="
                      "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs"), dl
    assert "--ignore-errors" in dl and "--download-archive" in dl
    assert dl[dl.index("--download-archive") + 1].endswith("downloaded.txt")
    out = dl[dl.index("-o") + 1]
    assert os.path.basename(os.path.dirname(out)) != "Downloads", out


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

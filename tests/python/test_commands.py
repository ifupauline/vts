# SPDX-License-Identifier: Apache-2.0
"""
The commands against the fake YouTube, on every system (Linux and
Windows alike): what they print, how they exit.
"""

import json
import os

from conftest import cache_dir, config_dir

from vts import __version__


def test_version_help(vts):
    for opt in ("-V", "--version"):
        r = vts(opt)
        assert (r.code, r.out, r.err) == (0, f"vts {__version__}\n", "")
    r = vts("--help")
    assert r.code == 0 and r.out.startswith("Usage: vts ")
    for args in (["--nope"], ["-x"], ["nosuchcommand"], ["--version=1"],
                 ["-c"]):
        r = vts(*args)
        assert r.code == 2 and r.out == "" and "vts --help" in r.err, r


def test_config(vts, env):
    r = vts("config", extra={"LANG": "fr_FR.UTF-8"})
    assert r.code == 0, r
    assert "(not found, defaults used)" in r.out
    assert "language = fr\nregion = FR\nquality = 1080\n" in r.out
    # explained: every setting, its values and its default
    r = vts("config", "--help")
    assert r.code == 0 and "thumbnails" in r.out and "Default:" in r.out
    assert "kitty" in r.out and "Values:" in r.out
    # changed from the command line, in the file
    r = vts("config", "set", "quality", "720")
    assert r.code == 0 and r.out == "quality = 720\n", r
    r = vts("config", "set", "quality", "999")
    assert r.code == 2 and "expected best" in r.err
    r = vts("config", "set", "colour", "red")
    assert r.code == 2 and "no setting colour" in r.err
    r = vts("config")
    assert "quality = 720\n" in r.out
    path = os.path.join(config_dir(env), "config")
    with open(path) as f:
        assert f.read() == "quality = 720\n"
    # --init: there is one already
    r = vts("config", "--init")
    assert r.code == 1 and "already exists" in r.err
    os.unlink(path)
    r = vts("config", "--init")
    assert r.code == 0, r
    with open(path) as f:
        text = f.read()
    assert "# values: best, 2160" in text and "\nquality = 1080\n" in text
    assert vts("config").code == 0
    assert vts("config", "extra").code == 2


def test_search(vts, youtube):
    r = vts("search", "linus", "torvalds")
    assert r.code == 0, r
    lines = r.out.splitlines()
    assert lines[0].split("\t") == [
        "video", "mfv0V1SxbNA",
        "Building the PERFECT Linux PC with Linus Torvalds",
        "Linus Tech Tips", "53:44", "9,590,258 views", "11 months ago"]
    assert all(len(line.split("\t")) == 7 for line in lines)
    r = vts("search", "-s", "views", "--type=video", "-d", "week", "-l",
            "long", "-f", "4k", "--feature", "subtitles", "linus torvalds")
    assert youtube.last_request()["params"] == "CAMSCggDEAEYAigBcAE="
    r = vts("search", "--json", "-t", "channel", "kurzgesagt")
    items = json.loads(r.out)
    assert items[0]["handle"] == "@kurzgesagt"
    r = vts("search", "-p", "2", "linus torvalds")
    assert len(r.out.splitlines()) >= len(lines) + 10
    for args in (["search"], ["search", "-t", "song", "x"],
                 ["search", "-p", "0", "x"], ["suggest"]):
        assert vts(*args).code == 2
    r = vts("search", "refused")
    assert r.code == 1 and "invalid argument" in r.err
    r = vts("search", "x", extra={"VTS_BASE_URL": "http://127.0.0.1:1"})
    assert r.code == 1 and "can't reach YouTube" in r.err


def test_suggest(vts):
    r = vts("suggest", "linus tor")
    assert r.code == 0 and "linus torvalds interview" in r.out.splitlines()
    assert "\x1b" not in r.out


def test_video_page(vts):
    r = vts("info", "https://youtu.be/mfv0V1SxbNA?t=4")
    assert r.code == 0, r
    lines = r.out.splitlines()
    assert lines[0] == "Building the PERFECT Linux PC with Linus Torvalds"
    assert "      2:42  Autobiography" in lines
    v = json.loads(vts("info", "--json", "dQw4w9WgXcQ").out)
    assert v["id"] == "dQw4w9WgXcQ" and v["comments"] is True
    assert vts("info", "notavideo").code == 2


def test_comments(vts):
    r = vts("comments", "mfv0V1SxbNA")
    lines = r.out.splitlines()
    assert r.code == 0 and lines[0].endswith(" Comments")
    assert lines[2].startswith("pinned · @LinusTechTips · creator")
    c = json.loads(vts("comments", "-j", "-r", "mfv0V1SxbNA").out)
    assert len(c) == 20 and c[0]["pinned"] is True
    assert c[0]["replies_list"][0]["kind"] == "comment"
    # the comments setting: newest first by default, --top for the others
    top = r.out
    newest = vts("comments", "--newest", "mfv0V1SxbNA").out
    assert newest != top
    assert vts("config", "set", "comments", "newest").code == 0
    assert vts("comments", "mfv0V1SxbNA").out == newest
    assert vts("comments", "--top", "mfv0V1SxbNA").out == top
    assert vts("config", "set", "comments", "oldest").code != 0


def test_channel_playlist_feed(vts):
    r = vts("channel", "@kurzgesagt")
    assert r.code == 0 and r.out.startswith("video\t"), r
    r = vts("channel", "-t", "playlists", "UCsXVk37bltHxD1rDPwtNM8Q")
    assert r.out.startswith("playlist\tPL")
    r = vts("channel", "@nobody")
    assert r.code == 1 and "no channel @nobody" in r.err
    r = vts("playlist", "https://www.youtube.com/watch?v=4_aOIA-vyBo"
            "&list=PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs")
    assert r.code == 0 and len(r.out.splitlines()) == 77
    r = vts("feed", "--no-shorts", "UCsXVk37bltHxD1rDPwtNM8Q")
    assert r.code == 0 and len(r.out.splitlines()) == 15


def test_library(vts, tmp_path):
    r = vts("fav", "add", "-g", "Science", "@kurzgesagt")
    assert r.code == 0 and r.out == "added Kurzgesagt – In a Nutshell\n"
    r = vts("fav")
    assert r.out.startswith("UCsXVk37bltHxD1rDPwtNM8Q\tKurzgesagt")
    r = vts("fav", "set", "@kurzgesagt", "notify=no", "shorts=no")
    assert r.code == 0
    f = json.loads(vts("fav", "--json").out)[0]
    assert (f["notify"], f["shorts"], f["group"]) == (False, False,
                                                      "Science")
    csv = tmp_path / "subs.csv"
    csv.write_text("Channel Id,Channel Url,Channel Title\n"
                   "UCXuqSBlHAE6Xw-yeJA0Tunw,http://x,Linus Tech Tips\n")
    r = vts("fav", "import", str(csv))
    assert r.out == "1 channel added\n"
    assert "<opml" in vts("fav", "export").out
    assert vts("fav", "remove", "@kurzgesagt").code == 0
    # Watch later and the playlists
    r = vts("later", "add", "mfv0V1SxbNA")
    assert r.code == 0, r
    r = vts("later")
    assert r.out.startswith("video\tmfv0V1SxbNA\tBuilding the PERFECT")
    assert vts("list", "create", "Music").code == 0
    assert vts("list").out == "Watch later\t1\nMusic\t0\n"
    assert vts("list", "delete", "Watch later").code == 1
    assert vts("history").out == ""


def test_check(vts, youtube, env):
    vts("fav", "add", "@kurzgesagt")
    r = vts("check", "-n")
    assert r.code == 0 and "first time" in r.err, r
    youtube.get("/set-feed?id=UCsXVk37bltHxD1rDPwtNM8Q"
                "&file=feed-UCsXVk37bltHxD1rDPwtNM8Q-new.xml")
    try:
        r = vts("check", "-n", "--json")
        got = [(i["kind"], i["id"]) for i in json.loads(r.out)]
        assert got == [("video", "NEWvideo001"), ("short", "NEWshort001")]
    finally:
        youtube.get("/set-feed?id=UCsXVk37bltHxD1rDPwtNM8Q"
                    "&file=feed-UCsXVk37bltHxD1rDPwtNM8Q.xml")


def test_check_live(vts, env, monkeypatch):
    # favourites on the three sites: some live (Lofi Girl twice, and more
    # it doesn't show; ComeBackTV; effort김정우), some not
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from vts.library import Library
    with Library() as lib:
        lib.fav_add("UCSJ4gkVC6NrvII8umztf0Ow", "Lofi Girl", "@LofiGirl")
        lib.fav_add("UCsXVk37bltHxD1rDPwtNM8Q", "Kurzgesagt", "@kurzgesagt")
        lib.fav_add("twitch:esl_sc2", "ESL_SC2", "esl_sc2")
        lib.fav_add("twitch:comebacktv", "ComeBackTV", "comebacktv")
        lib.fav_add("soop:momo130", "effort김정우", "momo130")
    r = vts("check", "-n")
    assert r.code == 0, r
    lines = [ln.split("\t") for ln in r.out.splitlines()]
    live = [ln[:2] + ln[3:5] for ln in lines if ln[4] == "LIVE"]
    assert live == [
        ["video", "4t4AQ15yj1M", "Lofi Girl", "LIVE"],
        ["video", "0sfDth4lMLc", "Lofi Girl", "LIVE"],
        ["video", "twitch:live:comebacktv", "ComeBackTV", "LIVE"],
        ["video", "soop:live:momo130", "effort김정우", "LIVE"]], r.out
    # not for a timer, nor as JSON, nor when asked not to
    assert vts("check", "-n", "-q").out == ""
    r = vts("check", "-n", "--no-live")
    assert "LIVE" not in r.out, r


def test_search_as_handle(vts):
    # YouTube finds nothing for these words: they are a channel's handle
    r = vts("search", "kurzgesagt")
    assert r.code == 0 and r.out.startswith(
        "channel\tUCsXVk37bltHxD1rDPwtNM8Q\t"), r
    assert "@kurzgesagt" in r.out, r
    r = vts("search", "nothing at all")
    assert r.code == 0 and r.out == "", r


def test_update_rules(vts, env, youtube):
    import urllib.request
    from vts import rules
    builtin = json.loads(rules.builtin_text())
    rev = builtin["revision"]
    req = urllib.request.Request(youtube.url + "/rules.json", data=json.dumps(
        dict(builtin, revision=rev + 1)).encode(), method="POST")
    urllib.request.urlopen(req, timeout=10).read()
    r = vts("update", "--rules")
    assert r.code == 0 and r.out == f"rules: revision {rev + 1} " \
        f"(was {rev})\ntwitch.json: up to date\nsoop.json: up to date\n", r
    assert os.path.exists(os.path.join(cache_dir(env), "rules.json"))
    assert vts("--offline", "update").code == 1


def test_doctor(vts):
    r = vts("doctor", extra={"PATH": ""})
    assert r.out.startswith(f"vts {__version__}\n")
    assert "XX  player" in r.out and "ok  search" in r.out
    assert r.code == 1

# SPDX-License-Identifier: Apache-2.0
"""The library, playback's bits and the thumbnails' geometry."""

import os
import sqlite3

import pytest

from vts import player, thumbs
from vts.library import EXISTS, NOT_FOUND, REFUSED, WATCH_LATER, LibError
from vts.library import Library
from vts.rules import Item

CHAN = "UCsXVk37bltHxD1rDPwtNM8Q"
CHAN2 = "UCXuqSBlHAE6Xw-yeJA0Tunw"


@pytest.fixture
def lib(tmp_path):
    with Library(str(tmp_path / "vts.db")) as lb:
        yield lb


def video(ident, **f):
    return Item("video", id=ident, **f)


def test_favourites(lib):
    lib.fav_add(CHAN, "Kurzgesagt", "@kurzgesagt", "Science")
    lib.fav_add(CHAN2, "Linus Tech Tips")
    with pytest.raises(LibError) as e:
        lib.fav_add(CHAN, "again")
    assert e.value.kind == EXISTS
    with pytest.raises(LibError):
        lib.fav_add("not a channel", "x")
    # by group, then name
    assert [f.name for f in lib.favs()] == ["Linus Tech Tips", "Kurzgesagt"]
    lib.fav_set(CHAN, "notify", "no")
    lib.fav_set(CHAN, "shorts", "off")
    lib.fav_set(CHAN, "group", "")
    f = lib.fav(CHAN)
    assert (f.notify, f.shorts, f.group, f.handle) == (False, False, "",
                                                        "@kurzgesagt")
    with pytest.raises(LibError):
        lib.fav_set(CHAN, "colour", "red")
    lib.fav_remove(CHAN)
    with pytest.raises(LibError) as e:
        lib.fav_remove(CHAN)
    assert e.value.kind == NOT_FOUND


def test_import_export(lib):
    n = lib.fav_import(
        b"\xef\xbb\xbfChannel Id,Channel Url,Channel Title\n"
        b"UCsXVk37bltHxD1rDPwtNM8Q,http://www.youtube.com/channel/x,"
        b'"Kurzgesagt, \x1b[31mIn a Nutshell"\n'
        b"not-a-channel,http://x,Nobody\n"
        b"UCXuqSBlHAE6Xw-yeJA0Tunw,http://x,\n")
    assert n == 2
    assert {f.name for f in lib.favs()} == {"Kurzgesagt, [31mIn a Nutshell",
                                            CHAN2}
    assert lib.fav_import(b"Channel Id,Channel Url,Channel Title\n" +
                          f"{CHAN},x,again\n".encode()) == 0
    opml = lib.fav_export()
    assert opml.startswith("<?xml") and f"channel_id={CHAN}" in opml
    assert "&" not in opml.replace("&amp;", "").replace("&quot;", "") \
        .replace("&lt;", "").replace("&gt;", "")
    csv = lib.fav_export(opml=False)
    assert '"Kurzgesagt, [31mIn a Nutshell"' in csv
    assert csv.startswith("Channel Id,Channel Url,Channel Title\n")
    # and back
    with Library(lib.file + "2") as other:
        assert other.fav_import(opml.encode()) == 2
        assert other.fav_import(csv.encode()) == 0
    for bad in (b"hello", b"<opml><!DOCTYPE x []>", b"a\0b"):
        with pytest.raises(LibError):
            lib.fav_import(bad)


def test_seen(lib):
    lib.fav_add(CHAN, "Kurzgesagt")
    assert not lib.seen_any(CHAN)
    it = video("aaaaaaaaaaa", channel_id=CHAN, title="A",
               date="2026-11-01T00:00:00+00:00")
    assert lib.seen_add(it) is True
    assert lib.seen_add(it) is False
    assert lib.seen_any(CHAN)
    short = Item("short", id="bbbbbbbbbbb", channel_id=CHAN, title="B",
                 date="2026-11-02T00:00:00+00:00")
    lib.seen_add(short)
    assert [i["id"] for i in lib.seen_recent()] == ["bbbbbbbbbbb",
                                                    "aaaaaaaaaaa"]
    lib.fav_set(CHAN, "shorts", "no")
    assert [i["id"] for i in lib.seen_recent()] == ["aaaaaaaaaaa"]


def test_history(lib):
    it = video("aaaaaaaaaaa", title="A", channel="C", duration="10:00")
    lib.hist_record(it, 30, 600)
    assert lib.hist_resume("aaaaaaaaaaa") == 30
    lib.hist_record(video("bbbbbbbbbbb"), 10, 0)
    # most recent first
    assert [w.item["id"] for w in lib.hist_list()] == ["bbbbbbbbbbb",
                                                       "aaaaaaaaaaa"]
    # at 90 %: watched, the next time from the start
    lib.hist_record(video("aaaaaaaaaaa"), 550, 600)
    w = lib.hist_list()[0]
    assert w.item["id"] == "aaaaaaaaaaa" and w.watched
    assert w.item["title"] == "A"  # kept from before
    assert lib.hist_resume("aaaaaaaaaaa") == 0
    marks = lib.hist_marks(["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"])
    assert marks == {"aaaaaaaaaaa": (550, 600, True),
                     "bbbbbbbbbbb": (10, 0, False)}
    lib.hist_remove("bbbbbbbbbbb")
    with pytest.raises(LibError):
        lib.hist_remove("bbbbbbbbbbb")
    lib.hist_clear()
    assert lib.hist_list() == []


def test_lists(lib):
    assert [pl.name for pl in lib.lists()] == [WATCH_LATER]
    lib.list_create("Music")
    lib.list_create("art")
    with pytest.raises(LibError) as e:
        lib.list_create("Music")
    assert e.value.kind == EXISTS
    with pytest.raises(LibError):
        lib.list_create("bad\nname")
    # Watch later first, then by name
    assert [pl.name for pl in lib.lists()] == [WATCH_LATER, "art", "Music"]
    lib.list_add("Music", video("aaaaaaaaaaa", title="A"))
    lib.list_add("Music", video("bbbbbbbbbbb", title="B"))
    with pytest.raises(LibError) as e:
        lib.list_add("Music", video("aaaaaaaaaaa"))
    assert e.value.kind == EXISTS
    assert [i["id"] for i in lib.list_items("Music")] == ["aaaaaaaaaaa",
                                                          "bbbbbbbbbbb"]
    assert lib.list_has("Music", "aaaaaaaaaaa")
    lib.list_remove("Music", "aaaaaaaaaaa")
    lib.list_rename("Music", "Songs")
    assert [i["id"] for i in lib.list_items("Songs")] == ["bbbbbbbbbbb"]
    for f in (lambda: lib.list_delete(WATCH_LATER),
              lambda: lib.list_rename(WATCH_LATER, "x")):
        with pytest.raises(LibError) as e:
            f()
        assert e.value.kind == REFUSED
    lib.list_delete("Songs")
    with pytest.raises(LibError):
        lib.list_items("Songs")


def test_searches(lib):
    for q in ("linux kernel", "lofi radio", "linus torvalds", "linux kernel"):
        lib.search_add(q)
    assert lib.searches() == ["linux kernel", "linus torvalds", "lofi radio"]
    assert lib.searches("lin") == ["linux kernel", "linus torvalds"]
    assert lib.searches("100%") == []
    lib.search_remove("lofi radio")
    assert "lofi radio" not in lib.searches()
    for i in range(60):
        lib.search_add(f"q{i}")
    assert len(lib.searches(limit=100)) == 50


def test_downloads(lib, tmp_path):
    lib.download_add(video("aaaaaaaaaaa", title="A"), str(tmp_path / "a.mkv"),
                     False)
    lib.download_add(video("bbbbbbbbbbb", title="B"), str(tmp_path / "b.mp3"),
                     True)
    d = lib.downloads()
    assert [x.item["title"] for x in d] == ["B", "A"]
    assert d[0].audio and not d[1].audio
    lib.download_remove(d[0].id)
    assert len(lib.downloads()) == 1


def test_upgrade(tmp_path):
    """a library of the first layout (made by the C vts) is brought on"""
    from vts import library
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    for stmt in library.SCHEMA_1.split(";\n"):
        if stmt.strip():
            db.execute(stmt)
    db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    with Library(path) as lb:
        lb.search_add("x")
        assert lb.searches() == ["x"]
        assert [pl.name for pl in lb.lists()] == [WATCH_LATER]
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {library.VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(LibError, match="newer vts"):
        Library(path)


def test_private(tmp_path):
    if os.name == "nt":
        pytest.skip("no modes on Windows")
    path = tmp_path / "vts.db"
    Library(str(path)).close()
    assert path.stat().st_mode & 0o077 == 0


# --- playback ---------------------------------------------------------------

def test_formats():
    assert player.ytdl_format(720) == ("bestvideo[height<=?720]+bestaudio/"
                                       "best[height<=?720]/best")
    assert player.ytdl_format(0) == "bestaudio/best"
    assert player.ytdl_format(-1) == "bestvideo+bestaudio/best"
    assert player.ytdl_sort(0, "high") is None
    assert player.ytdl_sort(0, "medium") == "abr~128"
    assert player.ytdl_sort(0, "low") == "abr~64"
    # the sound quality is for the sound only
    assert player.ytdl_sort(720, "low") is None
    assert player.play_name(0, "low") == "Sound only, low quality"
    assert player.play_name(720, "low") == "720p (HD)"
    assert player.quality_name(2160) == "2160p (4K)"
    with pytest.raises(ValueError):
        player.video_url("-o x")


def test_download_args():
    a = player.download_args("yt-dlp", "/d", [video("aaaaaaaaaaa")], 0, "mp3",
                             True)
    assert a[-2:] == ["--", "https://www.youtube.com/watch?v=aaaaaaaaaaa"]
    assert "--extract-audio" in a and "--audio-format" in a
    assert "--progress-template" in a
    with pytest.raises(ValueError):
        player.download_args("yt-dlp", "/d", [video("aaaaaaaaaaa")], 0, "exe")
    with pytest.raises(ValueError):
        player.download_args("yt-dlp", "/d", [video("aaaaaaaaaaa")], 0,
                             sound="loud")


@pytest.mark.parametrize("fmt, sound, sort, rate", [
    ("mp3", "high", None, "0"),
    ("mp3", "medium", "abr~128", "128K"),
    ("opus", "low", "abr~64", "64K"),
    ("flac", "low", "abr~64", "0"),
    (None, "medium", "abr~128", None),
])
def test_download_sound_quality(fmt, sound, sort, rate):
    a = player.download_args("yt-dlp", "/d", [video("aaaaaaaaaaa")], 0, fmt,
                             sound=sound)
    assert a[a.index("-f") + 1] == "bestaudio/best"
    if sort is None:
        assert "-S" not in a
    else:
        assert a[a.index("-S") + 1] == sort
    if rate is None:
        assert "--audio-quality" not in a
    else:
        assert a[a.index("--audio-quality") + 1] == rate


def test_progress():
    p = player.Progress()
    assert p.line("[download] Destination: /d/x.f137.mp4")
    assert p.file == "/d/x.f137.mp4" and p.files == 1
    assert p.line("vts-progress 500 1000 NA 2000000.5 3")
    assert (p.done, p.total, p.speed, p.eta, p.percent()) == (500, 1000,
                                                              2000000.5, 3,
                                                              50)
    assert p.line("vts-progress 500 NA 4000 NA NA")
    assert (p.total, p.speed, p.eta) == (4000, 0, -1)
    assert p.line('[Merger] Merging formats into "/d/x.mkv"')
    assert p.finishing and p.file == "/d/x.mkv"
    assert p.line("ERROR: \r[youtube] x: Video \x1b[31munavailable")
    assert p.error == "[youtube] x: Video [31munavailable"
    assert not p.line("[youtube] Extracting URL")


def test_why_nothing(tmp_path):
    f = tmp_path / "mpv.log"
    f.write_text("[   0.010][v][cplayer] options\n"
                 "[   0.400][e][ytdl_hook] ERROR: [youtube] x: see "
                 "https://github.com/yt-dlp/yt-dlp/wiki/FAQ for more\n"
                 "[   0.420][e][cplayer] Failed to recognize file format.\n")
    assert player.why_nothing(str(f)) == (
        "mpv played nothing: ERROR: [youtube] x: see https://github.com/"
        "… for more")
    f.write_text("[   0.420][e][cplayer] Failed to recognize file format.\n")
    assert player.why_nothing(str(f)).endswith("Failed to recognize file "
                                               "format.")
    assert "vts doctor" in player.why_nothing(str(tmp_path / "none"))


# --- thumbnails ---------------------------------------------------------------

def test_thumb_id():
    assert thumbs.thumb_id(video("aaaaaaaaaaa")) == "aaaaaaaaaaa"
    pl = Item("playlist", id="PLx", thumbnail="https://i.ytimg.com/vi/"
              "bbbbbbbbbbb/hqdefault.jpg")
    assert thumbs.thumb_id(pl) == "bbbbbbbbbbb"
    pl.f["thumbnail"] = "https://evil/vi/bbbbbbbbbbb/x.jpg"
    assert thumbs.thumb_id(pl) is None
    assert thumbs.thumb_id(Item("channel", id=CHAN)) is None


def test_fit():
    # a 16:9 picture in cells twice as high as wide
    assert thumbs.fit(320, 180, 38, 30, 10, 20) == (38, 11)
    assert thumbs.fit(320, 180, 38, 5, 10, 20) == (18, 5)


def test_render():
    pytest.importorskip("PIL")
    from PIL import Image
    img = Image.new("RGB", (32, 18), (200, 10, 10))
    text = thumbs.blocks_pillow(img, 8, 2, "full")
    assert text.count("\n") == 1 and "▀" in text
    assert "38;2;200;10;10" in text
    assert thumbs.kitty(img, 4, 2, 10, 20).startswith("\x1b_Ga=T,f=100")
    six = thumbs.sixel(img, 2, 1, 10, 20)
    assert six.startswith("\x1bPq") and six.endswith("\x1b\\")
    assert thumbs.iterm2(b"\xff\xd8xx", 4, 2).startswith("\x1b]1337;File=")


def test_windows_places(tmp_path, monkeypatch):
    """mpv found where its Windows installer puts it, off the PATH"""
    from vts import platform
    d = tmp_path / "MPV Player"
    d.mkdir()
    (d / "mpv.exe").write_bytes(b"")
    monkeypatch.setattr(platform, "WINDOWS", True)
    monkeypatch.setattr(platform.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert platform.find_program("mpv") == str(d / "mpv.exe")
    assert platform.find_program("yt-dlp") is None


@pytest.mark.parametrize("ident, channel, video", [
    ("UCsXVk37bltHxD1rDPwtNM8Q", True, False),
    ("dQw4w9WgXcQ", False, True),
    ("twitch:esl_sc2", True, False),
    ("twitch:live:esl_sc2", False, True),
    ("soop:brainzerg7", True, False),
    ("soop:live:brainzerg7", False, True),
    ("soop:vod:213143129", False, True),
    ("twitch:Upper", False, False),
    ("twitch:vod:123", False, False),
    ("soop:vod:12a", False, False),
    ("soop:x", False, False),
    ("youtube:dQw4w9WgXcQ", False, False),
    ("twitch:", False, False),
])
def test_site_ids(ident, channel, video):
    from vts import sites
    assert sites.valid_channel_id(ident) == channel
    assert sites.valid_video_id(ident) == video


def test_favourites_per_site(lib):
    from vts import sites
    lib.fav_add("UCsXVk37bltHxD1rDPwtNM8Q", "Kurzgesagt")
    lib.fav_add("twitch:esl_sc2", "ESL_SC2")
    lib.fav_add("soop:brainzerg7", "김윤환")
    # YouTube's by default: the check and the exports see only them
    assert [f.id for f in lib.favs()] == ["UCsXVk37bltHxD1rDPwtNM8Q"]
    assert [f.id for f in lib.favs("twitch")] == ["twitch:esl_sc2"]
    assert [f.id for f in lib.favs("soop")] == ["soop:brainzerg7"]
    assert len(lib.favs(None)) == 3
    assert "twitch" not in lib.fav_export() and \
        "soop" not in lib.fav_export(opml=False)
    assert sites.site_of(lib.favs("soop")[0].id) == "soop"
    # past broadcasts and live streams go in the history and lists too
    lib.hist_record(video("soop:vod:213143129", title="캄몬스타즈",
                          channel="김윤환", channel_id="soop:brainzerg7"),
                    120, 3600)
    assert lib.hist_resume("soop:vod:213143129") == 120
    lib.list_add("Watch later", video("soop:vod:213143129", title="x"))
    with pytest.raises(LibError):
        lib.fav_add("twitch:Not Valid", "x")


def test_site_settings():
    from vts import config
    c = config.Config()
    assert (c.start_site, c.twitch_quality, c.soop_quality,
            c.soop_vod_quality) == ("youtube", "best", "best", "best")
    c.set("twitch.quality", "720")
    c.set("soop.vod_quality", "audio")
    c.set("start_site", "last")
    assert c.get("twitch.quality") == "720" and c.twitch_quality == "720"
    assert c.get("soop.vod_quality") == "audio"
    for key, bad in (("twitch.quality", "540"), ("soop.quality", "160"),
                     ("start_site", "dailymotion")):
        with pytest.raises(config.BadValue):
            c.set(key, bad)
    assert [s.section for s in config.SETTINGS if s.key.startswith(
        ("twitch.", "soop."))] == ["Twitch"] * 4 + ["SOOP"] * 3
    assert {s.section for s in config.SETTINGS} == set(config.SECTIONS)

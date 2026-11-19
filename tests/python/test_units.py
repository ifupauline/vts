# SPDX-License-Identifier: Apache-2.0
"""The pieces, one by one: text, dates, rules, search, feeds, settings."""

import json
import os

import pytest
from conftest import FIXTURES

from vts import config, feed, rules, search, text, update, util
from vts.rules import Page, Rules

FFFD = "\ufffd"


# --- text ---------------------------------------------------------------------

@pytest.mark.parametrize("raw, clean", [
    # colours, window title, hyperlinks, cursor moves: all gone
    ("\x1b[31mred\x1b[0m", "[31mred[0m"),
    ("a\x1b]0;title\x07z", "a]0;titlez"),
    ("\x1b]8;;http://evil\x1b\\link\x1b]8;;\x1b\\",
     "]8;;http://evil\\link]8;;\\"),
    # the C1 forms of CSI and OSC
    ("x\x9b2Jy\x9dz", "x2Jyz"),
    ("a\x00b\x01\x08\x7fc\r", "abc"),
    ("tab\there", "tab here"),
    ("one\ntwo", "one two"),
    ("a\u2028b\u2029c", "a b c"),
    # right-to-left overrides that would show "exe.txt" as "txt.exe"
    ("evil\u202etxt.exe", "eviltxt.exe"),
    ("\u200e\u200f\u2066x\u2069\u061c", "x"),
    # lone surrogates, from JSON escapes
    ("a\ud800b", f"a{FFFD}b"),
    ("", ""),
])
def test_clean(raw, clean):
    assert text.clean(raw) == clean


def test_clean_lines():
    assert text.clean("one\ntwo\r\nthree", True) == "one\ntwo\nthree"
    assert text.clean("a\u2028b", True) == "a\nb"


def test_clean_bytes():
    out = text.clean(b"a\x80b\xff\x1b[2J")
    assert out == f"a{FFFD}b{FFFD}[2J"


def test_width():
    assert text.width("") == 0
    assert text.width("abc") == 3
    assert text.width("café") == 4
    assert text.width("cafe\u0301") == 4
    assert text.width("日本") == 4
    assert text.width("\U0001f3ac") == 2


@pytest.mark.parametrize("s, cols, out", [
    ("hello", 10, "hello"), ("hello", 5, "hello"), ("hello", 4, "hel…"),
    ("hello", 1, "…"), ("hello", 0, ""), ("", 3, ""),
    # a wide character doesn't fit in the last column
    ("ab日本", 5, "ab日…"), ("ab日本", 4, "ab…"),
    ("日本", 4, "日本"),
    # a combining accent stays with its letter
    ("cafe\u0301 noir", 5, "cafe\u0301…"),
])
def test_fit(s, cols, out):
    assert text.fit(s, cols) == out
    assert text.width(text.fit(s, cols)) <= max(cols, 0)


# --- small helpers ------------------------------------------------------------

def test_numbers():
    assert util.parse_long("1080", 0, 9999) == 1080
    assert util.parse_long("-5", -10, 10) == -5
    assert util.parse_long("0", 0, 0) == 0
    for bad in ("", " 5", "+5", "5x", "11", "١"):
        assert util.parse_long(bad, 0, 10) is None
    assert util.parse_bool("Yes") is True
    assert util.parse_bool("on") is True
    assert util.parse_bool("FALSE") is False
    assert util.parse_bool("yess") is None


def test_ago():
    t = util.iso_time("2026-11-12T16:00:37+00:00")
    assert util.days_from_civil(1970, 1, 1) == 0
    assert util.days_from_civil(2000, 3, 1) == 11017
    assert util.ago("2026-11-12T16:00:37+00:00", t + 30) == "just now"
    assert util.ago("2026-11-12T16:00:37+00:00", t + 61) == "1 minute ago"
    assert util.ago("2026-11-12T16:00:37Z", t + 3 * 3600) == "3 hours ago"
    assert util.ago("2026-11-12T17:00:37+01:00", t + 2 * 86400) == \
        "2 days ago"
    assert util.ago("2026-11-12T16:00:37.123-05:00", t + 5 * 3600 + 7 *
                    86400) == "1 week ago"
    assert util.ago("2024-11-12T16:00:37+00:00", t) == "2 years ago"
    assert util.ago("2026-08-12T16:00:37+00:00", t) == "3 months ago"
    for bad in ("2026-11-12", "2026-13-12T16:00:37Z",
                "2026-11-12T16:00:37junk", "11 months ago"):
        assert util.ago(bad, t) is None


def test_times():
    assert util.format_time(0) == "0:00"
    assert util.format_time(62) == "1:02"
    assert util.format_time(3723) == "1:02:03"
    assert util.parse_duration("1:02:03") == 3723
    assert util.parse_duration("x") is None


# --- rules ----------------------------------------------------------------

def page_of(doc, r=None):
    r = r or rules.builtin()
    return r.page(json.loads(doc), Page())


def test_builtin():
    r = rules.builtin()
    assert r.version == 1
    assert r.revision > 2026000000


@pytest.mark.parametrize("doc, reason", [
    ("[", "not valid JSON"),
    ('{"version": 2, "items": []}', "version"),
    ('{"version": 1}', "needs items"),
    ('{"version": 1, "items": [], "continuation_renderer": "c",'
     ' "skip": [1]}', "needs items"),
    ('{"version": 1, "items": [{"renderer": "x"}],'
     ' "continuation_renderer": "c"}', "item 0"),
    ('{"version": 1, "items": [{"renderer": "x", "kind": "video",'
     ' "fields": {"title": ["a"]}}], "continuation_renderer": "c"}',
     "no id field"),
    ('{"version": 1, "items": [{"renderer": "x", "kind": "song",'
     ' "fields": {"id": ["a"]}}], "continuation_renderer": "c"}', "item 0"),
    ('{"version": 1, "items": [], "continuation_renderer": "c",'
     ' "revision": "soon"}', "revision"),
    ('{"version": 1, "items": [], "continuation_renderer": "c",'
     ' "watch": {"related": "x"}}', "watch.related"),
])
def test_bad_rules(doc, reason):
    with pytest.raises(rules.BadRules, match=reason):
        Rules(doc)


def test_paths():
    j = json.loads(
        '{"a": {"list": [{"s": "x"}, {"s": "y", "style": "LIVE"},'
        ' {"s": "z"}]}, "t1": {"simpleText": "simple"},'
        ' "t2": {"runs": [{"text": "one "}, {"bold": 1}, {"text": "two"}]},'
        ' "t3": {"content": "vm \\u001b[2Jtext\\nnext"},'
        ' "n": 42, "e": [], "yes": true, "c": {"d": {"e": "f"}, "g": 1}}')
    p = rules.path
    assert p(j, "a.list.0.s") == "x"
    assert p(j, "a.list.-1.s") == "z"
    assert p(j, "a.list.*.style") == "LIVE"
    assert p(j, "a.list.*.style=LIVE") == "LIVE"
    assert p(j, "a.list.*.style=UPCOMING") is None
    assert p(j, "a.list.*.s=z") == "z"
    assert p(j, "e.-1") is None
    assert p(j, "e.*") is None
    assert p(j, "a.missing.*") is None
    assert p(j, "") is j
    assert p(j, "c.{d/e}.g") == 1
    assert p(j, "c.{d/x}.g") is None
    assert rules.text(p(j, "t1")) == "simple"
    assert rules.text(p(j, "t2")) == "one two"
    assert rules.text(p(j, "t3")) == "vm [2Jtext next"
    assert rules.text(p(j, "t3"), True) == "vm [2Jtext\nnext"
    assert rules.text(p(j, "n")) == "42"
    assert rules.text(p(j, "yes")) == "1"
    assert rules.text(p(j, "e")) == ""
    assert rules.text(None) == ""


def test_ids():
    assert rules.valid_video_id("dQw4w9WgXcQ")
    assert rules.valid_video_id("a-b_c-d_e-f")
    for bad in ("dQw4w9WgXc", "dQw4w9WgXcQQ", "--exec=rm", "../../../..",
                None):
        assert not rules.valid_video_id(bad)
    assert rules.valid_channel_id("UCsXVk37bltHxD1rDPwtNM8Q")
    assert not rules.valid_channel_id("XXsXVk37bltHxD1rDPwtNM8Q")
    assert not rules.valid_channel_id("UCsXVk37bltHxD1rDPwtNM8")
    assert rules.valid_playlist_id("PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs")
    assert rules.valid_playlist_id("WL")
    assert not rules.valid_playlist_id("P")
    assert not rules.valid_playlist_id("PL x")


def test_items():
    p = page_of(
        '{"contents": ['
        # an advertisement is left out
        ' {"adSlotRenderer": {"videoRenderer": {"videoId": "adadadadada"}}},'
        ' {"videoRenderer": {"videoId": "aaaaaaaaaaa",'
        '   "title": {"runs": [{"text": "A \\u001b]0;x\\u0007t"}]},'
        '   "lengthText": {"simpleText": "1:01"},'
        '   "thumbnail": {"thumbnails": [{"url": "https://i/s.jpg"},'
        '     {"url": "https://i/l.jpg"}]},'
        '   "longBylineText": {"runs": [{"text": "Chan",'
        '     "navigationEndpoint": {"browseEndpoint":'
        '     {"browseId": "UCsXVk37bltHxD1rDPwtNM8Q"}}}]}}},'
        # the same video again, and broken identifiers
        ' {"videoRenderer": {"videoId": "aaaaaaaaaaa"}},'
        ' {"videoRenderer": {"videoId": "-o exec=xy"}},'
        ' {"videoRenderer": {"title": {"simpleText": "no id"}}},'
        ' {"channelRenderer": {"channelId": "UCsXVk37bltHxD1rDPwtNM8Q",'
        '   "navigationEndpoint": {"browseEndpoint":'
        '     {"canonicalBaseUrl": "/@kurz_gesagt-1"}},'
        '   "thumbnail": {"thumbnails": [{"url": "//yt3/x.jpg"}]}}},'
        ' {"channelRenderer": {"channelId": "UCwRH985XgMYXQ6NxXDo8npw",'
        '   "navigationEndpoint": {"browseEndpoint":'
        '     {"canonicalBaseUrl": "/channel/x"}},'
        '   "thumbnail": {"thumbnails": [{"url": "javascript:alert(1)"}]}}},'
        ' {"continuationItemRenderer": {"continuationEndpoint":'
        '   {"continuationCommand": {"token": "NEXT"}}}}'
        '], "estimatedResults": "1234"}')
    assert len(p) == 3
    it = p.items[0]
    assert it.kind == "video"
    assert it.f == {"id": "aaaaaaaaaaa", "title": "A ]0;xt",
                    "duration": "1:01", "channel": "Chan",
                    "channel_id": "UCsXVk37bltHxD1rDPwtNM8Q",
                    "thumbnail": "https://i/l.jpg"}
    assert p.items[1].kind == "channel"
    assert p.items[1]["handle"] == "@kurz_gesagt-1"
    assert p.items[1]["thumbnail"] == "https://yt3/x.jpg"
    assert p.items[2]["handle"] is None
    assert p.items[2]["thumbnail"] is None
    assert p.continuation == "NEXT"
    assert p.estimated == "1234"
    # the last page has no token
    p = page_of('{"contents": [{"videoRenderer": {"videoId": '
                '"bbbbbbbbbbb"}}]}')
    assert len(p) == 1 and p.continuation is None


def test_collaboration():
    """a video by several channels: the first of them"""
    p = page_of(
        '{"contents":[{"videoRenderer":{"videoId":"sWbUDq4S6Y8",'
        '"longBylineText":{"runs":[{"text":"A and B",'
        '"navigationEndpoint":{"showDialogCommand":{'
        '"panelLoadingStrategy":{"inlineContent":{'
        '"dialogViewModel":{"customContent":{"listViewModel":{'
        '"listItems":[{"listItemViewModel":{"rendererContext":{'
        '"commandContext":{"onTap":{"innertubeCommand":{'
        '"browseEndpoint":{"browseId":"UC8butISFwT-Wl7EV0hUK0BQ"}}}}}}},'
        '{"listItemViewModel":{}}]}}}}}}}}]}}}]}')
    assert p.items[0]["channel"] == "A and B"
    assert p.items[0]["channel_id"] == "UC8butISFwT-Wl7EV0hUK0BQ"


def fixture(name):
    with open(os.path.join(FIXTURES, "youtube", name), "rb") as f:
        return f.read()


def test_real_search():
    with open(os.path.join(FIXTURES, "youtube", "index.json")) as f:
        index = json.load(f)["requests"]
    name = next(r["file"] for r in index if r["endpoint"] == "search" and
                r["match"].get("query") == "linus torvalds" and
                r["match"].get("hl") == "en" and "params" not in r["match"])
    r = rules.builtin()
    p = r.page(json.loads(fixture(name)), Page())
    assert len(p) >= 15
    assert p.items[0]["id"] == "mfv0V1SxbNA"
    assert sum(it.kind == "short" for it in p) >= 4
    assert p.continuation


# --- search ---------------------------------------------------------------

@pytest.mark.parametrize("what, value, website", [
    ("type", "video", "EgIQAQ=="), ("type", "shorts", "EgIQCQ=="),
    ("type", "channel", "EgIQAg=="), ("type", "playlist", "EgIQAw=="),
    ("type", "movie", "EgIQBA=="), ("duration", "short", "EgIYBA=="),
    ("duration", "medium", "EgIYBQ=="), ("duration", "long", "EgIYAg=="),
    ("date", "today", "EgIIAg=="), ("date", "week", "EgIIAw=="),
    ("date", "month", "EgIIBA=="), ("date", "year", "EgIIBQ=="),
    ("feature", "live", "EgJAAQ=="), ("feature", "4k", "EgJwAQ=="),
    ("feature", "hd", "EgIgAQ=="), ("feature", "subtitles", "EgIoAQ=="),
    ("feature", "cc", "EgIwAQ=="), ("feature", "360", "EgJ4AQ=="),
    ("feature", "vr180", "EgPQAQE="), ("feature", "3d", "EgI4AQ=="),
    ("feature", "hdr", "EgPIAQE="), ("feature", "location", "EgO4AQE="),
    ("feature", "purchased", "EgJIAQ=="), ("sort", "views", "CAM="),
    ("sort", "date", "CAI="), ("sort", "rating", "CAE="),
])
def test_website_params(what, value, website):
    """the same as the website sends, filter by filter"""
    o = search.Options()
    o.set(what, value)
    assert o.params() == website


def test_combined_params():
    o = search.Options()
    o.set("sort", "relevance")
    o.set("date", "any")
    assert o.params() == ""
    for what, value in (("sort", "views"), ("type", "video"),
                        ("date", "week"), ("duration", "long"),
                        ("feature", "4k,subtitles")):
        o.set(what, value)
    assert o.params() == "CAMSCggDEAEYAigBcAE="
    assert o.describe() == ("video, this week, over 20 min, 4k, subtitles, "
                            "by views")


@pytest.mark.parametrize("what, value", [
    ("type", "videos"), ("type", ""), ("colour", "red"),
    ("feature", "live,,4k"), ("feature", "live,nope"),
])
def test_bad_options(what, value):
    o = search.Options()
    with pytest.raises(ValueError):
        o.set(what, value)
    assert o.features == set()


# --- feeds ----------------------------------------------------------------

HEAD = ('<?xml version="1.0" encoding="UTF-8"?><feed '
        'xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" '
        'xmlns="http://www.w3.org/2005/Atom">')


def test_real_feed():
    page, name = feed.parse(fixture("feed-UCsXVk37bltHxD1rDPwtNM8Q.xml"))
    assert name == "Kurzgesagt – In a Nutshell"
    assert len(page) == 15
    assert any(it.kind == "short" for it in page)
    assert all(it["channel_id"] == "UCsXVk37bltHxD1rDPwtNM8Q"
               for it in page)


def test_hostile_feed():
    page, name = feed.parse((
        HEAD + "<title>A channel</title>"
        "<entry><yt:videoId>aaaaaaaaaaa</yt:videoId>"
        "<title>T\u009b2Jit\u202ele</title>"
        '<media:group><media:thumbnail url="javascript:alert(1)"/>'
        "<media:description>line 1&#10;line 2</media:description>"
        "</media:group></entry>"
        "<entry><yt:videoId>--exec=x yz</yt:videoId></entry>"
        "<entry><title>no id</title></entry>"
        "<entry><yt:videoId>aaaaaaaaaaa</yt:videoId></entry>"
        "</feed>").encode())
    assert name == "A channel"
    assert len(page) == 1
    it = page.items[0]
    assert it["title"] == "T2Jitle"
    assert it["thumbnail"] is None
    assert it["description"] == "line 1\nline 2"


@pytest.mark.parametrize("data", [
    # entities defined in a document type: refused outright
    '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaaaaaaaa">'
    '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    '<feed xmlns="http://www.w3.org/2005/Atom"><title>&b;</title></feed>',
    "<html><title>Sorry</title></html>",
    HEAD + "<title>\x1b[2J</title></feed>",
    "<rss><channel/></rss>", "", HEAD + "<entry><yt:videoId>aaaa", "<x/>",
])
def test_bad_feeds(data):
    with pytest.raises(feed.BadFeed):
        feed.parse(data.encode())


# --- settings -------------------------------------------------------------

@pytest.mark.parametrize("key, value, stored", [
    ("language", "FR", "fr"), ("language", "en-gb", "en-GB"),
    ("language", "zh-hant", "zh-Hant"), ("region", "fr", "FR"),
    ("region", "", ""), ("quality", "720", "720"), ("quality", "best", "best"),
    ("quality", "audio", "audio"), ("thumbnails", "yes", "auto"),
    ("thumbnails", "off", "no"), ("notifications", "off", "no"),
    ("downloads", "~/Videos", "~/Videos"), ("subtitles", "fr,en", "fr,en"),
    ("sound_quality", "low", "low"),
])
def test_settings(key, value, stored):
    c = config.Config()
    c.set(key, value)
    assert c.get(key) == stored


@pytest.mark.parametrize("key, value", [
    ("language", "french"), ("language", "f"), ("region", "FRA"),
    ("quality", "1000"), ("quality", "4k"), ("player", ""),
    ("player", "mpv\x1b"), ("subtitles", "fr;rm"), ("thumbnails", "maybe"),
    ("updates", "sometimes"), ("downloads", "Videos"),
    ("sound_quality", "best"), ("sound_quality", "128"),
])
def test_bad_settings(key, value):
    c = config.Config()
    with pytest.raises(config.BadValue):
        c.set(key, value)


def test_settings_file(tmp_path):
    p = tmp_path / "config"
    p.write_text("# comment\n\nquality = 720\nlanguage=de\n"
                 'subtitles = "fr,en"\nshiny = yes\n')
    c = config.Config()
    msgs = c.load(str(p))
    assert c.quality == 720 and c.language == "de" and c.subtitles == "fr,en"
    assert msgs == [f"{p}:6: unknown setting 'shiny': ignored"]
    p.write_text("quality = 1000\nnonsense\n")
    with pytest.raises(config.BadSettings) as e:
        config.Config().load(str(p))
    assert len(e.value.msgs) == 2


def test_locale(monkeypatch):
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "pt_BR.UTF-8@x")
    c = config.Config()
    assert (c.language, c.region) == ("pt", "BR")
    monkeypatch.setenv("LANG", "C")
    c = config.Config()
    assert (c.language, c.region) == ("en", "")


def test_edit_file(tmp_path):
    """a setting changed, the rest of the file kept as it was"""
    p = tmp_path / "config"
    p.write_text("# my settings\nquality = 720 \n# region = FR\n")
    config.edit_file(str(p), "quality", "1080")
    config.edit_file(str(p), "region", "FR")
    config.edit_file(str(p), "subtitles", "")
    assert p.read_text() == ("# my settings\nquality = 1080\n# region = FR\n"
                             "region = FR\nsubtitles =\n")
    config.edit_file(str(p), "region", None)
    assert "\nregion" not in p.read_text()
    c = config.Config()
    c.load(str(p))
    assert c.quality == 1080 and c.subtitles == ""


def test_init_text():
    """the explained file is a settings file giving the same settings"""
    c = config.Config()
    c.set("quality", "480")
    text_ = config.init_text(c)
    for s in config.SETTINGS:
        assert f"\n{s.key} =" in text_
    d = config.Config()
    lines = [line for line in text_.split("\n") if line and
             not line.startswith("#")]
    for line in lines:
        k, _, v = line.partition("=")
        d.set(k.strip(), v.strip())
    assert d.write() == c.write()


# --- updates --------------------------------------------------------------

def test_same_shape():
    ref = {"a": [1], "b": {"c": "x", "d": True}}
    assert update._same_shape(ref, {"a": [], "b": {"c": "y", "d": False},
                                    "new": 1}) is None
    assert "missing" in update._same_shape(ref, {"a": []})
    assert "type" in update._same_shape(ref, {"a": [], "b": {"c": 1,
                                                             "d": True}})


def test_ytdlp_age():
    assert update.ytdlp_age("2000.01.01") > 9000
    assert update.ytdlp_age("nightly") is None
    assert update.ytdlp_age("2026.13.01") is None


def test_term_write(capfd):
    from vts import platform
    platform.term_write("\x1b[31mé\x1b[0m")
    if not platform.WINDOWS:
        assert capfd.readouterr().out == "\x1b[31mé\x1b[0m"
    # on Windows: to the console shown, nothing at all without one

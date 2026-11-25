# SPDX-License-Identifier: Apache-2.0
"""Twitch and SOOP, against the recorded answers of the fake server"""
import datetime
import json

import pytest

from vts import sites
from vts.sites import twitch as tw


@pytest.fixture
def twitch(youtube, monkeypatch):
    monkeypatch.setenv("VTS_TWITCH_URL", youtube.url + "/gql")
    return tw.Twitch()


def test_twitch_find(twitch):
    found = twitch.find("esl_sc2")
    # the one named so first, then the others like it, once each
    assert found[0]["id"] == "twitch:esl_sc2"
    assert len({it["id"] for it in found}) == len(found) > 3
    first = found[0]
    assert first.kind == "channel" and first["title"] == "ESL_SC2"
    assert first["handle"] == "esl_sc2"
    assert first["subscribers"].endswith(" followers")
    assert "live" in first.f and first["game"] == "StarCraft II"
    assert first["stream_title"] and first["views"].endswith(" watching")
    assert all(it["id"].startswith("twitch:") for it in found)


def test_twitch_channel_and_status(twitch):
    c, live = twitch.channel("twitch:comebacktv")
    assert c["title"] == "ComeBackTV" and c["subscribers"] == \
        "27,541 followers"
    assert live.kind == "video" and live["id"] == "twitch:live:comebacktv"
    assert live["channel_id"] == "twitch:comebacktv" and "live" in live.f
    assert live["published"].startswith("on for ")
    got = twitch.status(["twitch:comebacktv", "twitch:esl_sc2"])
    assert [(c["id"], s is not None) for c, s in got] == [
        ("twitch:comebacktv", True), ("twitch:esl_sc2", False)]
    assert list(twitch.live(["twitch:comebacktv", "twitch:esl_sc2"])) == \
        ["twitch:comebacktv"]
    with pytest.raises(sites.SiteError):
        twitch.channel("twitch:nosuchone")


def test_twitch_batches(twitch, monkeypatch):
    asked = []

    def ask(query, variables):
        asked.append(len(variables["logins"]))
        return {"users": [None]}
    monkeypatch.setattr(twitch, "_ask", ask)
    twitch.users([f"user{i}" for i in range(250)] + ["user0", "Not OK"])
    assert asked == [100, 100, 50]


def test_twitch_errors(twitch, monkeypatch):
    from vts import http

    def answer(body, status=200):
        r = http.Response()
        r.status = status
        r.body = body
        return lambda req: r
    for body, status in ((b'{"errors": [{"message": "bad query"}]}', 200),
                         (b"<html>", 200), (b"{}", 503)):
        monkeypatch.setattr(http, "fetch", answer(body, status))
        with pytest.raises(sites.SiteError):
            twitch.find("x")


@pytest.mark.parametrize("text, ident", [
    ("https://www.twitch.tv/esl_sc2", "twitch:esl_sc2"),
    ("twitch.tv/ESL_SC2/videos", "twitch:esl_sc2"),
    ("https://m.twitch.tv/comebacktv?x=1", "twitch:comebacktv"),
    ("https://www.twitch.tv/directory/category/starcraft-ii", None),
    ("https://www.youtube.com/@esl", None),
    ("esl_sc2", None),
    ("https://evil.example/twitch.tv/x", None),
])
def test_twitch_links(twitch, text, ident):
    assert twitch.link(text) == ident


def test_on_for():
    now = datetime.datetime(2026, 11, 25, 12, 0, tzinfo=datetime.timezone.utc)
    assert tw.on_for("2026-11-25T11:15:00Z", now) == "on for 45 min"
    assert tw.on_for("2026-11-24T04:06:40Z", now) == "on for 31 h 53"
    assert tw.on_for("2026-11-26T00:00:00Z", now) is None
    assert tw.on_for("yesterday", now) is None


def test_twitch_items_cleaned():
    u = {"login": "abc", "displayName": "A\x1b[31mB", "stream": {
        "title": "t\x07", "viewersCount": "x", "type": "live",
        "previewImageURL": "http://insecure/x.jpg"}}
    c = tw.channel_item(u)
    live = tw.live_item(u)
    assert "\x1b" not in c["title"]
    assert "\x07" not in live["title"] and live["thumbnail"] is None
    assert tw.channel_item({"login": "Bad Login"}) is None
    assert tw.live_item({"login": "abc", "stream": None}) is None


def test_description():
    d = tw.load_description()
    assert d["url"].startswith("https://") and d["revision"] > 2026000000
    assert set(d["queries"]) >= {"users", "search"}
    # as on Twitch: plain queries with variables
    for q in d["queries"].values():
        assert q.startswith("query(") and "$" in q
    json.dumps(d)


def test_page_urls():
    assert sites.page_url("twitch:esl_sc2") == "https://www.twitch.tv/esl_sc2"
    assert sites.page_url("twitch:live:esl_sc2") == \
        "https://www.twitch.tv/esl_sc2"
    assert sites.page_url("soop:brainzerg7") == \
        "https://www.sooplive.co.kr/station/brainzerg7"
    assert sites.page_url("soop:vod:123") == \
        "https://vod.sooplive.com/player/123"


# --- SOOP ----------------------------------------------------------------

@pytest.fixture
def soop(youtube, monkeypatch):
    from vts.sites import soop as sp
    monkeypatch.setenv("VTS_SOOP_URL", youtube.url)
    return sp.Soop()


def test_soop_find(soop):
    found = soop.find("김정우")
    assert [it["id"] for it in found][:1] == ["soop:momo130"]
    first = found[0]
    assert first.kind == "channel" and first["title"] == "effort김정우"
    assert first["handle"] == "momo130" and first["subscribers"].endswith(
        " fans")
    assert first["thumbnail"].startswith("https://")
    # its id typed finds it too, first (and the search isn't needed)
    assert soop.find("@momo130")[0]["id"] == "soop:momo130"


def test_soop_channel_and_status(soop):
    c, live = soop.channel("soop:momo130")
    assert c["title"] == "effort김정우" and "live" in c.f
    assert live["id"] == "soop:live:momo130" and "live" in live.f
    assert live["views"].endswith(" watching")
    assert live["published"].startswith("on for ")
    assert live["locked"] is None
    got = soop.status(["soop:momo130", "soop:brainzerg7", "soop:nosuchbj"])
    assert [(c["id"], s is not None) for c, s in got] == [
        ("soop:momo130", True), ("soop:brainzerg7", False)]
    # what needs an account says so
    _, locked = soop.channel("soop:lockedbj")
    assert locked["locked"] == "19+"
    with pytest.raises(sites.SiteError):
        soop.channel("soop:nosuchbj")


def test_soop_past(soop):
    p = soop.past("soop:momo130")
    assert len(p.items) == 3 and p.continuation == 2 and p.estimated > 100
    v = p.items[0]
    assert v.kind == "video" and v["id"].startswith("soop:vod:")
    assert sites.valid_video_id(v["id"]) and not sites.is_live(v["id"])
    assert v["channel_id"] == "soop:momo130" and v["channel"] == \
        "effort김정우"
    assert v["duration"].count(":") >= 1 and v["views"].endswith(" views")
    assert v["published"].endswith(" ago")
    assert v["thumbnail"].startswith("https://videoimg.sooplive.com/")
    p2 = soop.past("soop:momo130", 2)
    assert p2.continuation is None and p2.items[0]["id"] != v["id"]


def test_soop_needs_a_browser(soop, monkeypatch):
    # SOOP closes the door to what doesn't say it is a browser
    soop.d = dict(soop.d, user_agent="vts")
    with pytest.raises(sites.SiteError):
        soop.channel("soop:momo130")


@pytest.mark.parametrize("text, ident", [
    ("https://www.sooplive.co.kr/station/momo130", "soop:momo130"),
    ("play.sooplive.co.kr/brainzerg7/298855055", "soop:brainzerg7"),
    ("https://ch.sooplive.co.kr/MOMO130", "soop:momo130"),
    ("https://vod.sooplive.com/player/213439935", "soop:vod:213439935"),
    ("https://vod.sooplive.co.kr/player/213439935/catch", "soop:vod:213439935"),
    ("https://bj.afreecatv.com/momo130", "soop:momo130"),
    ("https://www.sooplive.co.kr/directory/category", None),
    ("https://evil.example/station/momo130", None),
    ("https://www.twitch.tv/esl_sc2", None),
])
def test_soop_links(soop, text, ident):
    assert soop.link(text) == ident


def test_soop_on_for():
    from vts.sites import soop as sp
    assert sp.on_for("2026-11-25 18:38:44", "2026-11-25 22:06:00") == \
        "on for 3 h 27"
    assert sp.on_for("2026-11-25 18:38:44", "2026-11-25 18:50:44") == \
        "on for 12 min"
    assert sp.on_for("later", "2026-11-25 18:50:44") is None


def test_soop_description():
    from vts.sites import soop as sp
    d = sp.load_description()
    assert d["user_agent"].startswith("Mozilla/") and d["revision"] > 2026e6
    for k in ("search", "station", "past", "live_thumbnail"):
        assert d[k].startswith("https://")

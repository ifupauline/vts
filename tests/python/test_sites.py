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
        "https://vod.sooplive.co.kr/player/123"

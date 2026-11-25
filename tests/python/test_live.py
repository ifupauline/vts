# SPDX-License-Identifier: Apache-2.0
"""Twitch's and SOOP's live streams: played and recorded through
Streamlink (here a fake one, tests/fake_streamlink.py, and the fake mpv)"""
import json
import os
import time

import pytest

from conftest import FAKE_MPV, TESTS, WINDOWS
from vts import player, sites
from vts.rules import Item

pytestmark = pytest.mark.skipif(WINDOWS, reason="fake programs are scripts")

FAKE_STREAMLINK = os.path.join(TESTS, "fake_streamlink.py")


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    log = tmp_path / "fake.log"
    monkeypatch.setenv("VTS_STREAMLINK", FAKE_STREAMLINK)
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_WAIT", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return log


def entries(log, key):
    if not log.exists():
        return []
    with open(log) as f:
        return [e[key] for e in map(json.loads, f) if key in e]


def live(ident, title="StarCraft", channel="ESL_SC2"):
    site, _, name = sites.split(ident)
    return Item("video", id=ident, title=title, channel=channel,
                channel_id=sites.channel_id(site, name), live=True)


def wait_for(cond, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize("ident, quality, names, url", [
    ("twitch:live:esl_sc2", "720",
     "720p60,720p,720p30,480p60,480p,480p30,360p60,360p,360p30,160p60,160p,"
     "160p30,worst", "https://www.twitch.tv/esl_sc2"),
    ("twitch:live:esl_sc2", "audio", "audio_only,worst",
     "https://www.twitch.tv/esl_sc2"),
    ("soop:live:brainzerg7", "best", "best",
     "https://play.sooplive.co.kr/brainzerg7"),
    ("soop:live:brainzerg7", "audio", "worst",
     "https://play.sooplive.co.kr/brainzerg7"),
])
def test_live_plays_through_streamlink(fakes, ident, quality, names, url):
    o = player.PlayOptions()
    o.live_quality = quality
    p = player.Player(FAKE_MPV, None, [live(ident), live(ident)], o)
    try:
        assert wait_for(lambda: entries(fakes, "streamlink") and
                        entries(fakes, "argv")), fakes.read_text()
        sl = entries(fakes, "streamlink")[0]
        assert sl[-2:] == [url, names] and "--stdout" in sl
        mpv = entries(fakes, "argv")[0]
        # mpv reads it from Streamlink, alone: no queue, no yt-dlp
        assert mpv[-1] == "-" and mpv.count("-") == 1
        assert not any(a.startswith("--ytdl") for a in mpv)
        assert "--force-media-title=ESL_SC2: StarCraft" in mpv
        assert ("--no-video" in mpv) == (quality == "audio")
    finally:
        p.finish()
    # Streamlink stopped with mpv
    assert p.feeder.poll() is not None


def test_offline_says_so(fakes):
    o = player.PlayOptions()
    p = player.Player(FAKE_MPV, None, [live("twitch:live:offlinechan")], o)
    p.feeder.wait(10)
    os.environ["FAKE_WAIT"] = ""
    _, problem = p.finish()
    assert player.why_no_stream(p.feeder_log) == "Not live right now"


def test_recording(fakes, tmp_path):
    rec = player.Recording(str(tmp_path), live("soop:live:brainzerg7",
                                               "캄몬스타즈 / 결승?",
                                               "김윤환"), "720")
    assert wait_for(lambda: rec.poll() or rec.progress.done > 10000)
    assert rec.running()
    sl = entries(fakes, "streamlink")[0]
    assert sl[sl.index("--output") + 1] == rec.file()
    assert sl[-1].startswith("720p60,720p,720p30,540p60")
    name = os.path.basename(rec.file())
    # a file name for every system: no / or ?
    assert name.startswith("김윤환 ") and name.endswith(
        "캄몬스타즈 _ 결승_ [soop brainzerg7].ts"), name
    # stopping keeps what was recorded
    rec.cancel()
    assert rec.wait(10)
    assert rec.ok() and os.path.getsize(rec.file()) > 0


def test_recording_offline(fakes, tmp_path):
    rec = player.Recording(str(tmp_path), live("twitch:live:offline1"), "best")
    assert rec.wait(10)
    assert not rec.ok()
    assert "No playable streams" in rec.progress.error


def test_file_name():
    # control characters go, the rest of what Windows refuses becomes _
    assert player.file_name('a/b\\c:d*e?f"g<h>i|j\x07k') == \
        "a_b_c_d_e_f_g_h_i_jk"
    assert player.file_name("  ...  ") == "stream"
    assert len(player.file_name("x" * 300)) == 80

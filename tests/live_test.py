#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Checks vts against the real YouTube, to find out when YouTube changes
something vts reads. The other tests use recorded answers and keep
passing when YouTube moves on; this one doesn't. It needs the internet.

usage: live_test.py VTS

Each check runs a vts command and looks at the shape of what comes back
(not at its content, which changes every day): how many results, which
fields they have. Exit status: 0 when everything is read, 1 when
something isn't (YouTube changed: see what failed, then fix the rules or
the code and record new fixtures), 3 when YouTube refuses this network
for a while (its automated traffic check), which says nothing either
way.

Run by `make live-test` and by the daily live check.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

VIDEO = "dQw4w9WgXcQ"          # stays, has chapters off, comments on
CHANNEL = "@kurzgesagt"
CHANNEL_ID = "UCsXVk37bltHxD1rDPwtNM8Q"
PLAYLIST = "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs"
BLOCKED = "refusing requests from this network"
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
CHANNEL_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")

failures = []
blocked = []


def run(vts, env, *args):
    """vts @args as JSON; None when blocked (after a few tries)"""
    for attempt in range(3):
        p = subprocess.run([vts, *args], capture_output=True, text=True,
                           timeout=60, env=env)
        if BLOCKED not in p.stderr:
            break
        time.sleep(20 * (attempt + 1))
    else:
        blocked.append(" ".join(args))
        return None
    if p.returncode != 0:
        raise AssertionError(f"exit status {p.returncode}: "
                             f"{p.stderr.strip()[:300]}")
    if "--json" not in args:
        return p.stdout
    try:
        return json.loads(p.stdout)
    except ValueError:
        raise AssertionError(f"not JSON: {p.stdout[:200]!r}")


def has(item, *fields):
    missing = [f for f in fields if not item.get(f)]
    if missing:
        raise AssertionError(f"{item.get('kind')} {item.get('id')}: "
                             f"no {', '.join(missing)}")


def at_least(items, n, what):
    if len(items) < n:
        raise AssertionError(f"{len(items)} {what}, expected {n} or more")


def videos(items):
    return [i for i in items if i.get("kind") == "video"]


def check_search(vts, env):
    page = run(vts, env, "search", "--json", "linux")
    if page is None:
        return
    at_least(page, 10, "results")
    vids = videos(page)
    at_least(vids, 5, "videos")
    for v in vids:
        has(v, "id", "title", "channel", "channel_id", "views")
        assert VIDEO_ID.fullmatch(v["id"]), v["id"]
        assert CHANNEL_RE.fullmatch(v["channel_id"]), v["channel_id"]
    assert any(v.get("duration") for v in vids), "no durations"
    two = run(vts, env, "search", "--json", "--pages", "2", "linux")
    if two is not None:
        at_least(two, len(page) + 5, "results on two pages")


def check_filters(vts, env):
    page = run(vts, env, "search", "--json", "-t", "channel", "kurzgesagt")
    if page is None:
        return
    chans = [i for i in page if i.get("kind") == "channel"]
    at_least(chans, 1, "channels")
    has(chans[0], "id", "title", "subscribers")
    assert chans[0]["id"] == CHANNEL_ID, chans[0]["id"]
    page = run(vts, env, "search", "--json", "-t", "playlist", "kurzgesagt")
    if page is None:
        return
    lists = [i for i in page if i.get("kind") == "playlist"]
    at_least(lists, 3, "playlists")
    has(lists[0], "id", "title", "videos")
    page = run(vts, env, "search", "--json", "-d", "week", "-s", "views",
               "news")
    if page is not None:
        at_least(videos(page), 5, "videos of the week")


def check_video(vts, env):
    v = run(vts, env, "info", "--json", VIDEO)
    if v is None:
        return
    has(v, "id", "title", "channel", "channel_id", "views", "likes",
        "description", "date", "subscribers")
    assert "Rick Astley" in v["title"], v["title"]
    at_least(v.get("related", []), 5, "videos up next")
    for r in v["related"]:
        has(r, "id", "title")
    assert v.get("comments"), "no comments"


def check_chapters(vts, env):
    # a long video with chapters: the first of Linus Tech Tips' search
    page = run(vts, env, "search", "--json", "linus tech tips build guide")
    if page is None:
        return
    for item in videos(page)[:8]:
        v = run(vts, env, "info", "--json", item["id"])
        if v and v.get("chapters"):
            has(v["chapters"][0], "title")
            return
    raise AssertionError("no chapters in 8 videos")


def check_comments(vts, env):
    top = run(vts, env, "comments", "--json", VIDEO)
    if top is None:
        return
    at_least(top, 10, "comments")
    for c in top:
        has(c, "id", "channel")
    # a comment can be a picture alone: YouTube sends no text for it
    at_least([c for c in top if c.get("text")], len(top) - 2,
             "comments with their text")
    new = run(vts, env, "comments", "--json", "--newest", VIDEO)
    if new is not None:
        at_least(new, 10, "newest comments")
    rep = run(vts, env, "comments", "--json", "--replies", VIDEO)
    if rep is not None:
        with_replies = [c for c in rep if c.get("replies_list")]
        at_least(with_replies, 1, "comments with replies")
        has(with_replies[0]["replies_list"][0], "channel", "text")


def check_channel(vts, env):
    for tab, n in (("videos", 20), ("shorts", 20), ("live", 1),
                   ("playlists", 5)):
        c = run(vts, env, "channel", "--json", "-t", tab, CHANNEL)
        if c is None:
            return
        has(c, "id", "title", "handle", "subscribers")
        assert c["id"] == CHANNEL_ID, c["id"]
        at_least(c.get("items", []), n, f"items in the {tab} tab")
        has(c["items"][0], "id", "title")
    c = run(vts, env, "channel", "--json", "--search", "black hole",
            CHANNEL)
    if c is not None:
        at_least(c.get("items", []), 3, "results inside the channel")


def check_playlist(vts, env):
    p = run(vts, env, "playlist", "--json", PLAYLIST)
    if p is None:
        return
    has(p, "id", "title", "channel", "videos")
    at_least(p.get("items", []), 50, "videos in the playlist")
    for v in p["items"]:
        has(v, "id", "title")


def check_feed(vts, env):
    f = run(vts, env, "feed", "--json", CHANNEL)
    if f is None:
        return
    at_least(f, 10, "feed entries")
    for v in f:
        has(v, "id", "title", "date")
    f = run(vts, env, "feed", "--json", "--no-shorts", CHANNEL)
    if f is not None:
        at_least(f, 5, "feed entries without Shorts")
        assert all(v["kind"] == "video" for v in f), "Shorts left in"


def check_suggest(vts, env):
    out = run(vts, env, "suggest", "linus tor")
    if out is not None:
        at_least(out.splitlines(), 3, "suggestions")


def check_doctor(vts, env):
    """search, video page, feeds, suggestions and thumbnails all ok"""
    p = subprocess.run([vts, "doctor"], capture_output=True, text=True,
                       timeout=120, env=env)
    if BLOCKED in p.stdout:
        blocked.append("doctor")
    for what in ("search", "video page", "feeds", "suggestions",
                 "thumbnails"):
        line = next((l for l in p.stdout.splitlines()
                     if l[6:].startswith(what)), "")
        if BLOCKED in line:
            continue
        if not line.startswith("  ok  ") and \
                not (what == "thumbnails" and line.startswith("  --  ")):
            raise AssertionError(f"doctor: {line.strip() or what}")


CHECKS = [check_search, check_filters, check_video, check_chapters,
          check_comments, check_channel, check_playlist, check_feed,
          check_suggest, check_doctor]


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    vts = sys.argv[1]
    home = tempfile.mkdtemp(prefix="vts-live-")
    env = dict(os.environ, HOME=home, LANG="en_US.UTF-8", LC_ALL="",
               XDG_CONFIG_HOME=os.path.join(home, "config"),
               XDG_DATA_HOME=os.path.join(home, "data"),
               XDG_CACHE_HOME=os.path.join(home, "cache"),
               TERM="xterm-256color")
    for name in ("VTS_BASE_URL", "VTS_IMAGE_URL", "VTS_RULES_URL"):
        env.pop(name, None)
    for check in CHECKS:
        before = len(blocked)
        try:
            check(vts, env)
            status = "ok" if len(blocked) == before else "BLOCKED"
        except (AssertionError, subprocess.TimeoutExpired) as e:
            failures.append((check.__name__, str(e)))
            status = "FAIL"
        print(f"{check.__name__:30} {status}", flush=True)
    for name, why in failures:
        print(f"{name}: {why}")
    if blocked:
        print(f"YouTube refused this network for: {', '.join(blocked)}")
    if failures:
        return 1
    return 3 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())

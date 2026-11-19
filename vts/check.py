# SPDX-License-Identifier: Apache-2.0
"""
New videos of favourite channels: every favourite's feed is read (a few
at a time), each entry not seen before is new. The first time a channel
is checked, what its feed holds is only noted, so adding a favourite
doesn't bring fifteen notifications.
"""

from vts import feed, log, notify
from vts.rules import Page
from vts.youtube import AGAIN, YouTubeError

MAX_NOTES = 8  # at most this many notifications at once, then a sum up
PARALLEL = 8  # feeds read at the same time


class Check:
    def __init__(self):
        self.fresh = Page()  # the new videos, newest first per channel
        self.channels = 0  # favourites checked
        self.failed = 0  # feeds that couldn't be read
        self.first = 0  # channels checked for the first time


def _take_feed(lib, fav, data, c):
    try:
        page, _ = feed.parse(data)
    except feed.BadFeed:
        log.warn(f"{fav.name}: its feed can't be read")
        c.failed += 1
        return
    first = not lib.seen_any(fav.id)
    c.first += first
    for it in page.items:
        # the feed's channel is the favourite, whatever it says
        it.f["channel_id"] = fav.id
        it.f.setdefault("channel", fav.name)
        fresh = lib.seen_add(it)
        if not fresh or first or (not fav.shorts and it.kind == "short"):
            continue
        c.fresh.add(it)


def run(yt, lib):
    """check every favourite; LibError"""
    c = Check()
    favs = lib.favs()
    c.channels = len(favs)
    if not favs:
        return c
    # without Shorts when they don't count: a feed of its own
    paths = [feed.url("", f.id, not f.shorts) for f in favs]
    for f, r in zip(favs, yt.get_all(paths, feed.FEED_MAX, PARALLEL)):
        if isinstance(r, YouTubeError):
            log.warn(f"{f.name}: its feed can't be read ("
                     + ("automated traffic check" if r.kind == AGAIN
                        else str(r)) + ")")
            c.failed += 1
            continue
        _take_feed(lib, f, r, c)
    # what was seen of channels no longer favourites, after two months
    lib.seen_prune(60)
    return c


def tell(lib, c, notifier, actions=True):
    """
    a notification for each new video of the channels that want them, up
    to MAX_NOTES; how many were sent. FileNotFoundError when the program
    that notifies isn't installed.
    """
    sent = left = 0
    for it in c.fresh.items:
        fav = lib.fav(it["channel_id"])
        if not fav or not fav.notify:
            continue
        if sent >= MAX_NOTES:
            left += 1
            continue
        summary = fav.name + (" (Short)" if it.kind == "short" else "")
        notify.send(notifier, summary, it["title"] or it["id"],
                    it["id"] if actions else None)
        sent += 1
    if left:
        notify.send(notifier, "vts", f"{left} more new video"
                    f"{'' if left == 1 else 's'}")
        sent += 1
    return sent

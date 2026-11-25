# SPDX-License-Identifier: Apache-2.0
"""
The sites vts reads: YouTube, Twitch and SOOP.

YouTube's ids are YouTube's own ("dQw4w9WgXcQ", "UC..."). The others carry
their site, so that one library holds them all:

    twitch:LOGIN          a Twitch channel        (twitch:esl_sc2)
    twitch:live:LOGIN     what it streams now
    soop:BJID             a SOOP channel          (soop:brainzerg7)
    soop:live:BJID        what it broadcasts now
    soop:vod:NUMBER       one of its past broadcasts
"""

from vts import rules

SITES = ("youtube", "twitch", "soop")
NAMES = {"youtube": "YouTube", "twitch": "Twitch", "soop": "SOOP"}

_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


def _login(s, low, high):
    return low <= len(s) <= high and all(c in _LOWER for c in s)


def twitch_login(s):
    """a Twitch login: esl_sc2"""
    return isinstance(s, str) and _login(s, 1, 25)


def soop_id(s):
    """a SOOP channel's id: brainzerg7"""
    return isinstance(s, str) and _login(s, 2, 40)


_CHANNEL = {"twitch": twitch_login, "soop": soop_id}


def site_of(ident):
    """the site of an id: youtube, twitch or soop"""
    head = ident.split(":", 1)[0] if isinstance(ident, str) else ""
    return head if head in _CHANNEL else "youtube"


def split(ident):
    """ "soop:vod:123" -> ("soop", "vod", "123"); "twitch:abc" ->
    ("twitch", "", "abc"); a YouTube id -> ("youtube", "", ident)"""
    site = site_of(ident)
    if site == "youtube":
        return site, "", ident
    rest = ident[len(site) + 1:]
    what, sep, name = rest.partition(":")
    return (site, what, name) if sep else (site, "", rest)


def channel_id(site, name):
    """the library's id of the channel @name of @site"""
    return name if site == "youtube" else f"{site}:{name}"


def valid_channel_id(s):
    """a channel of any site"""
    if rules.valid_channel_id(s):
        return True
    site, what, name = split(s) if isinstance(s, str) else ("", "x", "")
    return site in _CHANNEL and not what and _CHANNEL[site](name)


def valid_video_id(s):
    """a video, a live stream or a past broadcast of any site"""
    if rules.valid_video_id(s):
        return True
    if not isinstance(s, str):
        return False
    site, what, name = split(s)
    if site == "twitch":
        return what == "live" and twitch_login(name)
    if site == "soop":
        if what == "live":
            return soop_id(name)
        return what == "vod" and name.isascii() and name.isdigit() and \
            len(name) <= 20
    return False


def sql_filter(site, column):
    """an SQL condition keeping the rows of @site, by the id in @column"""
    if site is None:
        return "1", ()
    if site == "youtube":
        return f"instr({column}, ':') = 0", ()
    return f"{column} LIKE ? ESCAPE '\\'", (site + ":%",)


class Site:
    """
    What vts asks of Twitch and SOOP (YouTube keeps its own, larger code:
    youtube.py, search.py, channel.py, video.py, feed.py). Each answer is
    made of rules.Item: channels ("channel") and what they broadcast
    ("video", with "live" for a live stream), their ids as above.
    """
    key = ""  # in SITES
    name = ""  # for people

    def find(self, query):
        """the channels whose name or id is like @query"""
        raise NotImplementedError

    def channel(self, ident):
        """the channel @ident as an Item; SiteError if there is none"""
        raise NotImplementedError

    def live(self, idents):
        """{channel id: its live stream as an Item} for those live now"""
        raise NotImplementedError

    def past(self, ident, page=1):
        """a page of the past broadcasts of @ident (rules.Page)"""
        return rules.Page()

    def link(self, text):
        """the channel or broadcast id a link of the site names, or None"""
        return None

    def stream_url(self, it):
        """the address Streamlink or yt-dlp plays @it from"""
        raise NotImplementedError


class SiteError(Exception):
    """a site couldn't be reached, or doesn't know what was asked"""


def get(key, cfg=None):
    """the Site for @key (twitch or soop), made when first asked"""
    raise KeyError(key)

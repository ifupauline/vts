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

    def status(self, idents):
        """
        [(channel Item, its live stream Item or None)] for the channels
        @idents that exist, in that order
        """
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


_made = {}


def get(key):
    """the Site for @key (twitch or soop), made when first asked"""
    if key not in _made:
        if key == "twitch":
            from vts.sites.twitch import Twitch
            _made[key] = Twitch()
        elif key == "soop":
            from vts.sites.soop import Soop
            _made[key] = Soop()
        else:
            raise KeyError(key)
    return _made[key]


# --- live streams: where they are, the qualities Streamlink names ---------

# the heights each site's live streams come in, highest first
LIVE_HEIGHTS = {"twitch": (1080, 720, 480, 360, 160),
                "soop": (1080, 720, 540, 360)}


def live_id(site, name):
    """the id of what the channel @name of @site streams now"""
    return f"{site}:live:{name}"


def live_url(ident):
    """the page Streamlink plays the live stream @ident from"""
    site, what, name = split(ident)
    if what != "live" or not _CHANNEL.get(site, lambda s: False)(name):
        raise ValueError("not a live stream")
    if site == "twitch":
        return f"https://www.twitch.tv/{name}"
    return f"https://play.sooplive.co.kr/{name}"


def page_url(ident):
    """the web page of a Twitch or SOOP channel, stream or broadcast"""
    site, what, name = split(ident)
    if site == "twitch":
        return f"https://www.twitch.tv/{name}"
    if what == "live":
        return f"https://play.sooplive.co.kr/{name}"
    if what == "vod":
        # yt-dlp knows SOOP's broadcasts by this address
        return f"https://vod.sooplive.com/player/{name}"
    return f"https://www.sooplive.co.kr/station/{name}"


def is_live(ident):
    """whether @ident is a Twitch or SOOP live stream"""
    return isinstance(ident, str) and split(ident)[1] == "live" and \
        valid_video_id(ident)


def stream_names(site, quality):
    """
    what to ask Streamlink for: the names of the streams it may pick, the
    first it has (720 -> 720p60, 720p, 720p30, then lower ones, then the
    lowest there is). @quality: best, a height or audio
    """
    if quality == "best":
        return "best"
    if quality == "audio":
        # SOOP has no sound-only stream: its lowest, mpv drops the picture
        return "audio_only,worst" if site == "twitch" else "worst"
    top = int(quality)
    names = []
    for h in LIVE_HEIGHTS.get(site, ()):
        if h <= top:
            names += [f"{h}p60", f"{h}p", f"{h}p30"]
    return ",".join(names + ["worst"])

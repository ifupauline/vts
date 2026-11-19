# SPDX-License-Identifier: Apache-2.0
"""
A channel's public feed (Atom): its latest uploads, what notifications
use, without an account or a key.
"""

import xml.parsers.expat

from vts.rules import Item, Page, check_item, valid_channel_id
from vts.text import clean
from vts.youtube import BAD_ANSWER, INVALID, YouTubeError

FEED_MAX = 2 * 1024 * 1024

NS_ATOM = "http://www.w3.org/2005/Atom|"
NS_YT = "http://www.youtube.com/xml/schemas/2015|"
NS_MEDIA = "http://search.yahoo.com/mrss/|"


class BadFeed(ValueError):
    pass


def url(base, channel_id, no_shorts=False):
    if not valid_channel_id(channel_id):
        raise ValueError(channel_id)
    # the uploads without Shorts: UULF and the id without its UC
    if no_shorts:
        return f"{base}/feeds/videos.xml?playlist_id=UULF{channel_id[2:]}"
    return f"{base}/feeds/videos.xml?channel_id={channel_id}"


class _Parse:
    def __init__(self, page):
        self.page = page
        self.channel = None
        self.item = None
        self.text = []
        self.size = 0
        self.depth = 0
        self.entry_depth = 0
        self.in_author = False
        self.is_feed = False

    def set_field(self, field, s, multiline=False):
        if s is None or field in self.item.f:
            return
        self.item.f[field] = clean(s, multiline)

    def start(self, name, atts):
        self.depth += 1
        self.text = []
        self.size = 0
        if self.depth == 1:
            self.is_feed = name == NS_ATOM + "feed"
            if not self.is_feed:
                raise BadFeed("not a feed")
            return
        if name == NS_ATOM + "entry" and not self.entry_depth:
            self.entry_depth = self.depth
            self.item = Item("video")
        elif name == NS_ATOM + "author":
            self.in_author = True
        elif not self.entry_depth:
            return
        elif name == NS_ATOM + "link":
            if atts.get("rel") == "alternate" and "/shorts/" in atts.get(
                    "href", ""):
                self.item.kind = "short"
        elif name == NS_MEDIA + "thumbnail":
            self.set_field("thumbnail", atts.get("url"))
        elif name == NS_MEDIA + "statistics":
            self.set_field("views", atts.get("views"))

    def end(self, name):
        t = "".join(self.text)
        if self.entry_depth and self.depth == self.entry_depth:
            if self.item.get("id") and check_item(self.item):
                self.page.add(self.item)
            self.item = None
            self.entry_depth = 0
        elif name == NS_ATOM + "author":
            self.in_author = False
        elif not self.entry_depth:
            # the channel's name: the feed's own title
            if (self.depth == 2 and name == NS_ATOM + "title" and
                    self.channel is None):
                self.channel = clean(t)
        elif name == NS_YT + "videoId":
            self.set_field("id", t)
        elif name == NS_YT + "channelId":
            self.set_field("channel_id", t)
        elif name == NS_ATOM + "title":
            self.set_field("title", t)
        elif name == NS_ATOM + "name" and self.in_author:
            self.set_field("channel", t)
        elif name == NS_ATOM + "published":
            self.set_field("date", t)
        elif name == NS_MEDIA + "description":
            if t:
                self.set_field("description", t, True)
        self.text = []
        self.size = 0
        self.depth -= 1

    def chars(self, s):
        self.size += len(s)
        if self.size > FEED_MAX:
            raise BadFeed("too long")
        self.text.append(s)


def _doctype(*args):
    # a feed has no document type: one would only serve entity tricks
    raise BadFeed("document type")


def parse(data):
    """(Page, channel name) of the feed @data (bytes); BadFeed"""
    if len(data) > FEED_MAX:
        raise BadFeed("too long")
    page = Page()
    p = _Parse(page)
    xp = xml.parsers.expat.ParserCreate(namespace_separator="|")
    xp.StartElementHandler = p.start
    xp.EndElementHandler = p.end
    xp.CharacterDataHandler = p.chars
    xp.StartDoctypeDeclHandler = _doctype
    xp.EntityDeclHandler = _doctype
    try:
        xp.Parse(data, True)
    except xml.parsers.expat.ExpatError:
        raise BadFeed("not XML") from None
    if not p.is_feed:
        raise BadFeed("not a feed")
    return page, p.channel


def get(yt, channel_id, no_shorts=False):
    """(Page, channel name) of @channel_id's feed; YouTubeError"""
    try:
        path = url("", channel_id, no_shorts)
    except ValueError:
        raise YouTubeError("not a channel id", INVALID) from None
    data = yt.get(path, FEED_MAX)
    try:
        return parse(data)
    except BadFeed:
        raise YouTubeError("the channel's feed can't be read",
                           BAD_ANSWER) from None

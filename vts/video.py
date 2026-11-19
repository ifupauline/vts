# SPDX-License-Identifier: Apache-2.0
"""The video page: details, chapters, what comes next; its comments."""

from vts import util
from vts.rules import Item, Page, check_item, fill, path, valid_video_id
from vts.youtube import NOT_FOUND, YouTubeError


class Chapter:
    __slots__ = ("start", "title")

    def __init__(self, start, title):
        self.start = start
        self.title = title


class Video:
    def __init__(self):
        self.info = Item("video")  # id, title, channel, views, likes...
        self.chapters = []  # Chapter, in order
        self.related = Page()  # "Up next"
        self.comments_token = None  # None when comments are off

    def to_json(self):
        o = self.info.to_json()
        o["chapters"] = [{"start": c.start, "title": c.title}
                         for c in self.chapters]
        o["related"] = self.related.to_json()
        o["comments"] = self.comments_token is not None
        return o


def _chapters(r, doc):
    lst = None
    for p in r.section("watch.chapters.lists") or ():
        lst = path(doc, p)
        if isinstance(lst, list):
            break
        lst = None
    fields = r.section("watch.chapters.fields")
    out = []
    for c in lst or ():
        it = Item("video")
        fill(fields, c, it)
        ms = util.parse_long(it.get("start_ms"), 0, 1000 * 3600 * 1000)
        if it.get("title") and ms is not None:
            out.append(Chapter(ms // 1000, it.f["title"]))
    return out


def get(yt, ident):
    """the page of the video @ident; YouTubeError"""
    if not valid_video_id(ident):
        raise YouTubeError("not a video identifier", "invalid")
    r = yt.rules
    doc = yt.call("next", {"videoId": ident})
    v = Video()
    v.info.f["id"] = ident
    fill(r.section("watch.fields"), doc, v.info)
    check_item(v.info)
    if not v.info.get("title"):
        raise YouTubeError(f"no video {ident} (or it is private)", NOT_FOUND)
    v.chapters = _chapters(r, doc)
    r.page_under("watch.related", doc, v.related)
    v.comments_token = r.first("watch.comments", doc)
    return v


def ref(s):
    """the video identifier in @s (an id or a link), or None"""
    if valid_video_id(s):
        return s
    for marker in ("?v=", "&v=", "youtu.be/", "/shorts/", "/live/", "/embed/",
                   "/v/"):
        i = s.find(marker)
        if i < 0:
            continue
        rest = s[i + len(marker):]
        ident = rest[:11]
        # the identifier ends there: "&t=42", "?si=...", "#x" or nothing
        if len(ident) == 11 and (len(rest) == 11 or rest[11] in "&?#/") \
                and valid_video_id(ident):
            return ident
    return None


def comments_more(yt, token, page):
    """the comments at @token, added to @page"""
    return yt.rules.comments(yt.call("next", {"continuation": token}), page)


def comments_first(yt, token, newest=False):
    """the first page of comments, top ones or the newest first"""
    page = Page()
    doc = yt.call("next", {"continuation": token})
    if newest:
        t = yt.rules.first("comments.sort.newest", doc)
        if not t:
            raise YouTubeError("no newest-first order", NOT_FOUND)
        return comments_more(yt, t, page)
    return yt.rules.comments(doc, page)

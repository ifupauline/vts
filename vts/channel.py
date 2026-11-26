# SPDX-License-Identifier: Apache-2.0
"""Channels and playlists: their header and their videos."""

from vts.rules import (Item, Page, check_item, fill, valid_channel_id,
                       valid_playlist_id)
from vts.youtube import INVALID, IO, NOT_FOUND, YouTubeError

TABS = ("videos", "shorts", "live", "playlists", "podcasts", "posts",
        "search")
# the end of each tab's address: YouTube answers with the channel's Home
# for a tab it doesn't have
TAB_PATHS = {"videos": ("/videos",), "shorts": ("/shorts",),
             "live": ("/streams", "/live"), "playlists": ("/playlists",),
             "podcasts": ("/podcasts",), "posts": ("/posts", "/community")}


class Browse:
    def __init__(self, kind):
        self.info = Item(kind)
        self.page = Page()
        self.missing = False  # a channel without the tab asked for

    def to_json(self):
        o = self.info.to_json()
        o["items"] = self.page.to_json()
        return o


def _segment_after(s, marker):
    """ "name" or "name/..." after @marker in @s, or None"""
    i = s.find(marker)
    if i < 0:
        return None
    rest = s[i + len(marker):]
    n = len(rest)
    for j, c in enumerate(rest):
        if c in "/?#&":
            n = j
            break
    return rest[:n] if 0 < n < 128 else ""


def _name_ok(s):
    """handles and custom names: letters, digits, . _ - and non-ASCII"""
    return all(ord(c) >= 0x80 or c in "._-" or (c.isascii() and c.isalnum())
               for c in s)


def handle_like(s):
    """whether @s could be a handle (without its @)"""
    return bool(s) and _name_ok(s)


def resolve(yt, ref):
    """the channel id of @ref: an id, a handle, a link; YouTubeError"""
    if valid_channel_id(ref):
        return ref
    name = _segment_after(ref, "/channel/")
    if name and valid_channel_id(name):
        return name
    url = None
    # a handle, or a link YouTube has to turn into an id
    if ref.startswith("@") and len(ref.encode()) < 128 and len(ref) > 1 and \
            _name_ok(ref[1:]):
        url = f"https://www.youtube.com/{ref}"
    else:
        for kind in ("/@", "/c/", "/user/"):
            name = _segment_after(ref, kind)
            if name and _name_ok(name):
                url = f"https://www.youtube.com{kind}{name}"
                break
    if not url:
        raise YouTubeError(f"not a channel: {ref}", INVALID)
    try:
        doc = yt.call("navigation/resolve_url", {"url": url})
    except YouTubeError as e:
        if e.kind in (INVALID, IO):
            raise YouTubeError(f"no channel {ref}", NOT_FOUND) from None
        raise
    found = yt.rules.first("channel.resolve", doc)
    if not found or not valid_channel_id(found):
        raise YouTubeError(f"no channel {ref}", NOT_FOUND)
    return found


def _browse(yt, fields, section, b):
    doc = yt.call("browse", fields)
    r = yt.rules
    fill(r.section(f"{section}.fields"), doc, b.info)
    check_item(b.info)
    r.page_under(f"{section}.results", doc, b.page)
    post_titles(b.page)
    return doc


def post_titles(page):
    """a title for each post: its first line, or what it holds"""
    for it in page.items:
        if it.kind != "post" or it.get("title"):
            continue
        first = (it.get("text") or "").strip().split("\n")[0]
        if len(first) > 100:
            first = first[:99] + "\u2026"
        if not first:
            if it["video"]:
                first = it.get("video_title") or "Video"
            elif it["poll"]:
                first = "Poll"
            else:
                first = "Picture" if it["thumbnail"] else "Post"
        it.f["title"] = first


def get(yt, ident, tab="videos", query=None):
    """the channel @ident and the results of @tab; YouTubeError"""
    if not valid_channel_id(ident) or tab not in TABS or (
            tab == "search" and not query):
        raise YouTubeError("no such channel or tab", INVALID)
    params = yt.rules.section(f"channel.tabs.{tab}")
    fields = {"browseId": ident}
    if isinstance(params, str):
        fields["params"] = params
    if tab == "search":
        fields["query"] = query
    b = Browse("channel")
    doc = _browse(yt, fields, "channel", b)
    if b.info.get("id") != ident:
        raise YouTubeError(f"no channel {ident}", NOT_FOUND)
    # a tab the channel doesn't have: nothing, not its Home
    shown = yt.rules.first("channel.tab", doc)
    if tab in TAB_PATHS and shown and not shown.rstrip("/").endswith(
            TAB_PATHS[tab]):
        b.page.items.clear()
        b.page.continuation = None
        b.missing = True
    return b


def playlist_ref(s):
    """the playlist identifier in @s (an id or a link with list=), or None"""
    if valid_playlist_id(s) and "/" not in s:
        return s
    i = s.find("list=")
    if i < 0:
        return None
    rest = s[i + 5:]
    for j, c in enumerate(rest):
        if c in "&#":
            rest = rest[:j]
            break
    return rest if valid_playlist_id(rest) else None


def playlist(yt, ident):
    """the playlist @ident and its videos; YouTubeError"""
    if not valid_playlist_id(ident):
        raise YouTubeError(f"not a playlist: {ident}", INVALID)
    b = Browse("playlist")
    b.info.f["id"] = ident
    _browse(yt, {"browseId": "VL" + ident}, "playlist", b)
    if not b.info.get("title"):
        raise YouTubeError(f"no playlist {ident} (or it is private)",
                           NOT_FOUND)
    return b


def more(yt, section, b):
    """the next page of @b, added to it; False when there is none"""
    if not b.page.continuation:
        return False
    doc = yt.call("browse", {"continuation": b.page.continuation})
    yt.rules.page_under(f"{section}.results", doc, b.page)
    post_titles(b.page)
    return True

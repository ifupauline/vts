#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Record real YouTube answers as test fixtures, so the tests check vts on
what YouTube really sends without needing the internet. Run it again
when YouTube changes: the tests then show what vts no longer reads.

usage: record.py [CASE...]   (all cases without arguments)

Writes tests/fixtures/youtube/CASE.json, trimmed of what vts never reads
(tracking data, menus, sharing panels) to keep them small, and
index.json, which tells fake_server.py which answer fits which request.
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "youtube")
BASE = "https://www.youtube.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 "
      "Firefox/140.0")
VERSION = "2.20261111.01.00"

# name: (endpoint, request fields, language, region)
CASES = {
    "search-linus": ("search", {"query": "linus torvalds"}, "en", "US"),
    "search-linus-fr": ("search", {"query": "linus torvalds"}, "fr", "FR"),
    "search-channels": ("search", {"query": "kurzgesagt",
                                   "params": "EgIQAg=="}, "en", "US"),
    "search-playlists": ("search", {"query": "kurzgesagt",
                                    "params": "EgIQAw=="}, "en", "US"),
    "search-live": ("search", {"query": "lofi radio",
                               "params": "EgJAAQ=="}, "en", "US"),
}
# videos: their page, without (en) and with region (fr)
CASES.update({
    "next-mfv0V1SxbNA": ("next", {"videoId": "mfv0V1SxbNA"}, "en", "US"),
    "next-dQw4w9WgXcQ": ("next", {"videoId": "dQw4w9WgXcQ"}, "en", "US"),
})
# channels and playlists
KURZ = "UCsXVk37bltHxD1rDPwtNM8Q"
CASES.update({
    "resolve-kurzgesagt": ("navigation/resolve_url",
                           {"url": "https://www.youtube.com/@kurzgesagt"},
                           "en", "US"),
    "channel-videos": ("browse", {"browseId": KURZ,
                                  "params": "EgZ2aWRlb3PyBgQKAjoA"},
                       "en", "US"),
    "channel-shorts": ("browse", {"browseId": KURZ,
                                  "params": "EgZzaG9ydHPyBgUKA5oBAA=="},
                       "en", "US"),
    "channel-playlists": ("browse",
                          {"browseId": KURZ,
                           "params": "EglwbGF5bGlzdHPyBgoKCEIGCgIQaCIA"},
                          "en", "US"),
    "channel-search": ("browse", {"browseId": KURZ,
                                  "params": "EgZzZWFyY2jyBgQKAloA",
                                  "query": "black hole"}, "en", "US"),
    "playlist-existential": ("browse",
                             {"browseId": "VLPLFs4vir_WsTxontcYm5ctqp89cNBJKNrs"},
                             "en", "US"),
})
# channel feeds (XML, kept as they are): name: path
FEEDS = {
    "feed-UCsXVk37bltHxD1rDPwtNM8Q":
        "/feeds/videos.xml?channel_id=UCsXVk37bltHxD1rDPwtNM8Q",
    "feed-UULFsXVk37bltHxD1rDPwtNM8Q":
        "/feeds/videos.xml?playlist_id=UULFsXVk37bltHxD1rDPwtNM8Q",
}

# answers to a token found in another recording:
# name: (recorded case, which token, endpoint)
NEXT_PAGES = {
    "search-linus-2": ("search-linus", "results", "search"),
    "comments-mfv0V1SxbNA": ("next-mfv0V1SxbNA", "comments", "next"),
    "comments-mfv0V1SxbNA-2": ("comments-mfv0V1SxbNA", "results", "next"),
    "comments-mfv0V1SxbNA-newest": ("comments-mfv0V1SxbNA", "newest",
                                    "next"),
    "replies-mfv0V1SxbNA": ("comments-mfv0V1SxbNA", "replies", "next"),
    "channel-videos-2": ("channel-videos", "results", "browse"),
}

# never read by vts, in any answer
DROP = {
    "trackingParams", "clickTrackingParams", "loggingDirectives",
    "loggingContext", "frameworkUpdates", "topbar", "responseContext",
    "commandMetadata", "webCommandMetadata", "accessibility",
    "accessibilityData", "unifiedSharePanelRenderer", "menu",
    "watchEndpointSupportedOnesieConfig", "richThumbnail",
    "searchFilterOptionsDialogRenderer", "inlinePlaybackEndpoint",
    "adSignalsInfo", "playerParams", "expandableMetadata", "sequenceParams",
    "buttonViewModel", "toggleButtonViewModel", "genAiFeedbackFormViewModel",
    "videoSummaryContentViewModel", "colorData", "openPopupAction",
    "chipCloudRenderer", "likeButtonViewModel", "dislikeButtonViewModel",
    "engagementPanels", "styleRuns", "styleRunExtensions", "themedPalette",
    "horizontalCardListRenderer", "cards", "customEmojis", "createRenderer",
    "emojiPicker", "engagementToolbarSurfaceEntityPayload",
    "commentSurfaceEntityPayload", "pageVisualEffects", "overlay",
}
# read on video pages (likes, comments): kept there
KEEP_FOR = {
    "next": {"buttonViewModel", "toggleButtonViewModel", "likeButtonViewModel",
             "frameworkUpdates"},
}


def trim(o, drop):
    if isinstance(o, dict):
        return {k: trim(v, drop) for k, v in o.items() if k not in drop}
    if isinstance(o, list):
        return [trim(x, drop) for x in o]
    return o


def call(endpoint, fields, hl, gl):
    body = {"context": {"client": {"clientName": "WEB",
                                   "clientVersion": VERSION,
                                   "hl": hl, "gl": gl}}}
    body.update(fields)
    req = urllib.request.Request(
        f"{BASE}/youtubei/v1/{endpoint}?prettyPrint=false",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Cookie": "SOCS=CAI", "X-YouTube-Client-Name": "1",
                 "X-YouTube-Client-Version": VERSION})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            # the automated traffic check comes and goes
            if e.code not in (403, 429) or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def walk(o, fn):
    if isinstance(o, dict):
        for k, v in o.items():
            if not fn(k, v):
                walk(v, fn)
    elif isinstance(o, list):
        for x in o:
            walk(x, fn)


def token(doc, which):
    """a token in @doc: of the next results, the comments, the replies"""
    found = []

    def cont(v):
        try:
            return v["continuationEndpoint"]["continuationCommand"]["token"]
        except KeyError:
            return None

    def results(k, v):
        # not inside comment threads (replies) nor in the header
        if k in ("commentThreadRenderer", "header"):
            return True
        if k == "continuationItemRenderer" and cont(v):
            found.append(cont(v))
        return False

    def comments(k, v):
        if k == "itemSectionRenderer" and \
                v.get("sectionIdentifier") == "comment-item-section":
            found.append(cont(v["contents"][0]["continuationItemRenderer"]))
            return True
        return False

    def newest(k, v):
        if k == "sortFilterSubMenuRenderer":
            found.append(v["subMenuItems"][1]["serviceEndpoint"]
                         ["continuationCommand"]["token"])
            return True
        return False

    def replies(k, v):
        if k == "commentRepliesRenderer" and not found:
            found.append(cont(v["contents"][0]["continuationItemRenderer"]))
            return True
        return False

    walk(doc, {"results": results, "comments": comments, "newest": newest,
               "replies": replies}[which])
    return found[-1] if which == "results" and found else \
        (found[0] if found else None)


def save(name, doc, endpoint):
    drop = DROP - KEEP_FOR.get(endpoint, set())
    with open(os.path.join(OUT, name + ".json"), "w") as f:
        json.dump(trim(doc, drop), f, ensure_ascii=False,
                  separators=(",", ":"))
        f.write("\n")


def record_feed(name):
    req = urllib.request.Request(BASE + FEEDS[name],
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    with open(os.path.join(OUT, name + ".xml"), "wb") as f:
        f.write(data)
    print(f"{name}: {len(data)} bytes")


def main():
    wanted = sys.argv[1:] or list(CASES) + list(NEXT_PAGES) + list(FEEDS)
    for name in [n for n in wanted if n in FEEDS]:
        record_feed(name)
    wanted = [n for n in wanted if n not in FEEDS]
    index_path = os.path.join(OUT, "index.json")
    with open(index_path) as f:
        index = json.load(f)
    def key(e):
        return e["endpoint"] + json.dumps(e["match"], sort_keys=True)
    entries = {key(e): e for e in index["requests"]}
    for name in wanted:
        if name in CASES:
            endpoint, fields, hl, gl = CASES[name]
            doc = call(endpoint, fields, hl, gl)
            match = dict(fields, hl=hl)
        else:
            first, which, endpoint = NEXT_PAGES[name]
            with open(os.path.join(OUT, first + ".json")) as f:
                tok = token(json.load(f), which)
            doc = call(endpoint, {"continuation": tok}, "en", "US")
            match = {"continuation": tok}
        save(name, doc, endpoint)
        e = {"endpoint": endpoint, "match": match, "file": name + ".json"}
        # a new token replaces the entry of the earlier recording
        for k in [k for k, old in entries.items() if old["file"] == e["file"]]:
            del entries[k]
        entries[key(e)] = e
        print(f"{name}: {os.path.getsize(os.path.join(OUT, name + '.json'))}"
              " bytes")
        time.sleep(2)
    index["requests"] = sorted(entries.values(), key=lambda e: e["file"])
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    sys.exit(main())

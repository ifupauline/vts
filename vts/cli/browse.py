# SPDX-License-Identifier: Apache-2.0
"""search, suggest, info, comments, channel, playlist, feed"""

import sys

from vts import channel as ch
from vts import feed, log, platform, search, util, video
from vts.cli import (EXIT_FAIL, EXIT_OK, Usage, details, length_of, out,
                     pages_arg, parse, parts, print_indented, print_items,
                     print_json, try_help)
from vts.youtube import Client, YouTubeError

SEARCH_USAGE = """\
Usage: vts search [OPTION]... WORDS...
Search YouTube, with the website's filters.

  -t, --type TYPE         video, shorts, channel, playlist, movie
  -d, --date WHEN         hour, today, week, month, year
  -l, --duration LENGTH   short (under 3 min), medium (3-20 min),
                          long (over 20 min)
  -f, --feature LIST      live, 4k, hd, subtitles, cc, 360, vr180,
                          3d, hdr, location, purchased (comma-separated)
  -s, --sort ORDER        relevance, date, views, rating
  -p, --pages N           read N pages of results (default 1)
  -j, --json              print JSON
  -h, --help              show this help

On a terminal, two lines per result; otherwise one line of
tab-separated fields: kind, id, title, channel or handle,
duration, views or subscribers or videos, date.
"""


def cmd_search(cfg, argv):
    opts, args = parse("search", argv, "t:d:l:f:s:p:jh",
                       ["type=", "date=", "duration=", "feature=", "sort=",
                        "pages=", "json", "help"])
    so = search.Options()
    pages = 1
    as_json = False
    names = {"-t": "type", "--type": "type", "-d": "date", "--date": "date",
             "-l": "duration", "--duration": "duration", "-f": "feature",
             "--feature": "feature", "-s": "sort", "--sort": "sort"}
    for o, v in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(SEARCH_USAGE)
            return EXIT_OK
        if o in ("-p", "--pages"):
            pages = pages_arg("search", v)
        elif o in ("-j", "--json"):
            as_json = True
        else:
            what = names[o]
            try:
                so.set(what, v)
            except ValueError:
                print(f"vts search: --{what}: expected {search.VALUES[what]}",
                      file=sys.stderr)
                raise Usage() from None
    if not args:
        print("vts search: what to search for?", file=sys.stderr)
        try_help("search")
        raise Usage()
    yt = Client(cfg)
    failed = False
    try:
        page = search.search(yt, " ".join(args), so)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    try:
        for _ in range(1, pages):
            if not search.more(yt, page):
                break
    except YouTubeError as e:
        # what was found before a later page failed is still shown
        log.err(str(e))
        failed = True
        if not page.items:
            return EXIT_FAIL
    if as_json:
        print_json(page.to_json())
    else:
        print_items(page.items)
    q = " ".join(args)
    if not page.items and len(args) == 1 and (q.startswith("@") or
                                               "youtu" in q):
        # YouTube's search often finds nothing for those
        log.warn(f"nothing found; for the channel: vts channel {q}"
                 if q.startswith("@") else "nothing found; for what the "
                 f"link names: vts info, vts channel or vts playlist {q}")
    return EXIT_FAIL if failed else EXIT_OK


def cmd_suggest(cfg, argv):
    if not argv or argv[0] in ("--help", "-h"):
        print("Usage: vts suggest WORDS...\n"
              "What YouTube's search box suggests for WORDS, one per line.",
              file=sys.stdout if argv else sys.stderr)
        return EXIT_OK if argv else 2
    yt = Client(cfg)
    try:
        for s in yt.suggest(" ".join(argv)):
            out(s)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    return EXIT_OK


def video_arg(cmd, args):
    """the video identifier of the command's argument"""
    if len(args) != 1:
        print(f"vts {cmd}: which video? (its id or a link)", file=sys.stderr)
        try_help(cmd)
        raise Usage()
    ident = video.ref(args[0])
    if not ident:
        print(f"vts {cmd}: not a video id or link: {args[0]}",
              file=sys.stderr)
        raise Usage()
    return ident


def print_video(v, related, dislikes=None):
    from vts.dislikes import short
    it = v.info
    out(it.get("title"))
    out(parts(it["channel"], it["handle"], it["subscribers"]))
    likes = it["likes"]
    out(parts("LIVE" if "live" in it.f else None, it["views"], it["date"],
              f"{likes} likes" if likes else None,
              f"{short(dislikes)} dislikes" if dislikes is not None
              else None))
    out(f"https://www.youtube.com/watch?v={it.get('id')}")
    if it["description"]:
        out()
        print_indented(it["description"], 0)
    if v.chapters:
        out("\nChapters:")
    for c in v.chapters:
        out(f"  {util.format_time(c.start):>8}  {c.title}")
    if related > 0 and v.related.items:
        out("\nUp next:")
    for r in v.related.items[:related]:
        out(f"  {r.get('id')}  {length_of(r):<8} {r.get('title')} · "
            f"{r.get('channel')}")
    if not v.comments_token:
        out("\nComments are turned off.")


INFO_USAGE = """\
Usage: vts info [OPTION]... VIDEO
The video page: title, channel, views, date, likes (and
dislikes, with the dislikes setting), description, chapters
and the videos that come next.
VIDEO is its id or any YouTube link to it.

  -r, --related N  show N videos up next (default 5)
  -j, --json       print JSON (with all of them)
  -h, --help       show this help
"""


def cmd_info(cfg, argv):
    opts, args = parse("info", argv, "r:jh", ["related=", "json", "help"])
    related = 5
    as_json = False
    for o, v in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(INFO_USAGE)
            return EXIT_OK
        if o in ("-r", "--related"):
            related = util.parse_long(v, 0, 100)
            if related is None:
                print("vts info: --related: 0 to 100", file=sys.stderr)
                raise Usage()
        else:
            as_json = True
    ident = video_arg("info", args)
    yt = Client(cfg)
    try:
        v = video.get(yt, ident)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    dis = None
    if cfg.dislikes and not cfg.offline:
        from vts import dislikes
        dis = dislikes.get(ident)
    if as_json:
        o = v.to_json()
        if dis is not None:
            o["dislikes"] = dis
        print_json(o)
    else:
        print_video(v, related, dis)
    return EXIT_OK


def comment_line(it):
    likes = it["likes"]
    replies = it["replies"]
    return parts("pinned" if "pinned" in it.f else None, it["channel"],
                 "creator" if "creator" in it.f else None, it["published"],
                 f"{likes} likes" if likes and likes != "0" else None,
                 f"{replies} replies" if replies and replies != "0" else None)


def print_comment(it, indent):
    out(" " * indent + comment_line(it))
    print_indented(it.get("text"), indent + 2)
    out()


COMMENTS_USAGE = """\
Usage: vts comments [OPTION]... VIDEO
The comments of a video, top ones first (or as the comments
setting says).

  -n, --newest   newest first
  -t, --top      top ones first
  -p, --pages N  read N pages of comments (default 1)
  -r, --replies  with the first replies to each
  -j, --json     print JSON
  -h, --help     show this help
"""


def cmd_comments(cfg, argv):
    opts, args = parse("comments", argv, "ntp:rjh",
                       ["newest", "top", "pages=", "replies", "json",
                        "help"])
    newest = cfg.comments == "newest"
    with_replies = as_json = False
    pages = 1
    for o, v in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(COMMENTS_USAGE)
            return EXIT_OK
        if o in ("-n", "--newest"):
            newest = True
        elif o in ("-t", "--top"):
            newest = False
        elif o in ("-p", "--pages"):
            pages = pages_arg("comments", v)
        elif o in ("-r", "--replies"):
            with_replies = True
        else:
            as_json = True
    ident = video_arg("comments", args)
    yt = Client(cfg)
    failed = False
    try:
        v = video.get(yt, ident)
        if not v.comments_token:
            log.err("comments are turned off for this video")
            return EXIT_FAIL
        page = video.comments_first(yt, v.comments_token, newest)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    try:
        for _ in range(1, pages):
            if not page.continuation:
                break
            video.comments_more(yt, page.continuation, page)
    except YouTubeError as e:
        log.err(str(e))
        failed = True
        if not page.items:
            return EXIT_FAIL
    replies = []
    if with_replies:
        for it in page.items:
            from vts.rules import Page
            rp = Page()
            if it["replies_token"]:
                try:
                    video.comments_more(yt, it["replies_token"], rp)
                except YouTubeError as e:
                    log.warn(f"replies: {e}")
            replies.append(rp)
    if as_json:
        items = page.to_json()
        for o, rp in zip(items, replies):
            o["replies_list"] = rp.to_json()
        print_json(items)
    else:
        if page.estimated:
            out(f"{page.estimated}\n")
        for i, it in enumerate(page.items):
            print_comment(it, 0)
            for r in replies[i].items if replies else ():
                print_comment(r, 4)
    return EXIT_FAIL if failed else EXIT_OK


def print_header(it):
    """a channel or playlist header, on a terminal only (scripts get TSV)"""
    if not platform.term_cols():
        return
    out(it.get("title"))
    out(parts(it["handle"], it["channel"], it["subscribers"], it["videos"],
              it["views"]))
    if it["description"]:
        # its first line, the rest is one command away
        out(it["description"].split("\n")[0])
    out()


def show_browse(yt, section, b, pages, as_json):
    failed = False
    try:
        for _ in range(1, pages):
            if not ch.more(yt, section, b):
                break
    except YouTubeError as e:
        log.err(str(e))
        failed = True
    if as_json:
        print_json(b.to_json())
    else:
        print_header(b.info)
        print_items(b.page.items)
    return EXIT_FAIL if failed else EXIT_OK


CHANNEL_USAGE = """\
Usage: vts channel [OPTION]... CHANNEL
A channel's videos, Shorts, live streams, playlists, podcasts
or posts. CHANNEL is its id (UC...), its handle (@name) or a
link.

  -t, --tab TAB      videos (default), shorts, live, playlists,
                     podcasts, posts
  -s, --search WORDS search the channel
  -p, --pages N      read N pages (default 1)
  -j, --json         print JSON
  -h, --help         show this help
"""


def cmd_channel(cfg, argv):
    opts, args = parse("channel", argv, "t:s:p:jh",
                       ["tab=", "search=", "pages=", "json", "help"])
    tab = "videos"
    query = None
    pages = 1
    as_json = False
    for o, v in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(CHANNEL_USAGE)
            return EXIT_OK
        if o in ("-t", "--tab"):
            if v not in ch.TABS or v == "search":
                print("vts channel: --tab: videos, shorts, live, playlists, "
                      "podcasts or posts", file=sys.stderr)
                raise Usage()
            tab = v
        elif o in ("-s", "--search"):
            tab = "search"
            query = v
        elif o in ("-p", "--pages"):
            pages = pages_arg("channel", v)
        else:
            as_json = True
    if len(args) != 1:
        print("vts channel: which channel? (id, @handle or link)",
              file=sys.stderr)
        raise Usage()
    yt = Client(cfg)
    try:
        b = ch.get(yt, ch.resolve(yt, args[0]), tab, query)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    if b.missing:
        log.warn(f"{b.info.get('title') or args[0]} has no {tab} tab")
    return show_browse(yt, "channel", b, pages, as_json)


PLAYLIST_USAGE = """\
Usage: vts playlist [OPTION]... PLAYLIST
The videos of a playlist. PLAYLIST is its id or a link
with list=ID.

  -p, --pages N  read N pages (default 1)
  -j, --json     print JSON
  -h, --help     show this help
"""


def cmd_playlist(cfg, argv):
    opts, args = parse("playlist", argv, "p:jh", ["pages=", "json", "help"])
    pages = 1
    as_json = False
    for o, v in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(PLAYLIST_USAGE)
            return EXIT_OK
        if o in ("-p", "--pages"):
            pages = pages_arg("playlist", v)
        else:
            as_json = True
    ident = ch.playlist_ref(args[0]) if len(args) == 1 else None
    if not ident:
        print("vts playlist: which playlist? (id or link)", file=sys.stderr)
        raise Usage()
    yt = Client(cfg)
    try:
        b = ch.playlist(yt, ident)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    return show_browse(yt, "playlist", b, pages, as_json)


FEED_USAGE = """\
Usage: vts feed [OPTION]... CHANNEL
A channel's latest uploads from its feed, newest first:
what notifications use. CHANNEL is its id, @handle or a link.

  -n, --no-shorts  without Shorts
  -j, --json       print JSON
  -h, --help       show this help
"""


def cmd_feed(cfg, argv):
    opts, args = parse("feed", argv, "njh", ["no-shorts", "json", "help"])
    no_shorts = as_json = False
    for o, _ in opts:
        if o in ("-h", "--help"):
            sys.stdout.write(FEED_USAGE)
            return EXIT_OK
        if o in ("-n", "--no-shorts"):
            no_shorts = True
        else:
            as_json = True
    if len(args) != 1:
        print("vts feed: which channel? (id, @handle or link)",
              file=sys.stderr)
        raise Usage()
    yt = Client(cfg)
    try:
        page, _ = feed.get(yt, ch.resolve(yt, args[0]), no_shorts)
    except YouTubeError as e:
        log.err(str(e))
        return EXIT_FAIL
    if as_json:
        print_json(page.to_json())
    else:
        print_items(page.items)
    return EXIT_OK


__all__ = ["details"]

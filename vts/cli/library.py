# SPDX-License-Identifier: Apache-2.0
"""fav, history, list, later"""

import sys

from vts import channel as ch
from vts import feed, log, platform, util, video
from vts.cli import (EXIT_FAIL, EXIT_OK, EXIT_USAGE, out, print_items,
                     print_json)
from vts.library import EXISTS, WATCH_LATER, LibError, open_library
from vts.rules import Item, valid_channel_id
from vts.youtube import Client, YouTubeError

FAV_USAGE = """\
Usage: vts fav [COMMAND [ARGUMENT]...]
Your favourite channels, kept on your computer, no account.

  vts fav                       list them
  vts fav add [-g GROUP] CHANNEL...
                                add channels (id, @handle or link)
  vts fav remove CHANNEL...     remove channels
  vts fav set CHANNEL notify=yes|no shorts=yes|no group=NAME
                                desktop notifications, whether its
                                Shorts count, its group
  vts fav import FILE           add the channels of Google Takeout's
                                subscriptions.csv or of an OPML file
  vts fav export [--csv]        print them as OPML (or as CSV)
  vts fav --json                list them as JSON
"""


def yn(b):
    return "yes" if b else "no"


class _Yt:
    """a YouTube client made when first needed"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.yt = None

    def get(self):
        if not self.yt:
            self.yt = Client(self.cfg)
        return self.yt


def fav_ref(lib, yt, ref):
    """the id of a favourite named by @ref, without the network if possible"""
    if valid_channel_id(ref):
        return ref
    if ref.startswith("@"):
        for f in lib.favs():
            if f.handle and f.handle.lower() == ref.lower():
                return f.id
    try:
        return ch.resolve(yt.get(), ref)
    except YouTubeError as e:
        log.err(str(e))
        return None


def add_favourite(lib, yt, ident, group=None):
    """
    make the channel @ident a favourite, its name and handle from its page
    (or its name from its feed); (name, whether it was new); YouTubeError,
    LibError
    """
    name = handle = None
    try:
        b = ch.get(yt, ident)
        name = b.info["title"]
        handle = b.info["handle"]
    except YouTubeError:
        _, name = feed.get(yt, ident)
    name = name or ident
    try:
        lib.fav_add(ident, name, handle, group)
    except LibError as e:
        if e.kind == EXISTS:
            return name, False
        raise
    return name, True


def fav_add(lib, cfg, group, refs):
    yt = Client(cfg)
    status = EXIT_OK
    for ref in refs:
        try:
            ident = ch.resolve(yt, ref)
            name, new = add_favourite(lib, yt, ident, group)
        except (YouTubeError, LibError) as e:
            log.err(str(e))
            status = EXIT_FAIL
            continue
        out(f"added {name}" if new else f"{name} is already a favourite")
    return status


def fav_show(lib, as_json):
    favs = lib.favs()
    if as_json:
        print_json([f.to_json() for f in favs])
        return EXIT_OK
    tty = platform.term_cols() > 0
    for i, f in enumerate(favs):
        if not tty:
            out("\t".join((f.id, f.name, f.handle or "", f.group,
                           yn(f.notify), yn(f.shorts))))
            continue
        if f.group and (i == 0 or favs[i - 1].group != f.group):
            out(f"{chr(10) if i else ''}{f.group}:")
        out(f"  {f.name}" + (f" · {f.handle}" if f.handle else "") +
            ("" if f.notify else " · no notifications") +
            ("" if f.shorts else " · no Shorts"))
    if tty and not favs:
        out("No favourites yet: vts fav add @channel")
    return EXIT_OK


def fav_import(lib, file):
    try:
        data = platform.read_file(file, 16 << 20, binary=True)
    except (OSError, ValueError) as e:
        log.err(f"{file}: {getattr(e, 'strerror', None) or e}")
        return EXIT_FAIL
    try:
        added = lib.fav_import(data)
    except LibError as e:
        log.err(f"{file}: {e}")
        return EXIT_FAIL
    out(f"{added} channel{'' if added == 1 else 's'} added")
    return EXIT_OK


def cmd_fav(cfg, argv):
    cmd = argv[0] if argv else ""
    if cmd in ("--help", "-h", "help"):
        sys.stdout.write(FAV_USAGE)
        return EXIT_OK
    if (cmd not in ("", "--json", "add", "remove", "set", "import", "export")
            or (cmd == "remove" and len(argv) < 2)
            or (cmd == "set" and len(argv) < 3)
            or (cmd == "import" and len(argv) != 2)):
        sys.stderr.write(FAV_USAGE)
        return EXIT_USAGE
    group = None
    refs = argv[1:]
    if cmd == "add":
        import getopt
        try:
            opts, refs = getopt.getopt(argv[1:], "g:")
        except getopt.GetoptError:
            sys.stderr.write(FAV_USAGE)
            return EXIT_USAGE
        for _, v in opts:
            group = v
        if not refs:
            sys.stderr.write(FAV_USAGE)
            return EXIT_USAGE
    if cmd == "export" and len(argv) > (2 if argv[1:2] == ["--csv"] else 1):
        sys.stderr.write(FAV_USAGE)
        return EXIT_USAGE
    lib = open_library()
    if not lib:
        return EXIT_FAIL
    yt = _Yt(cfg)
    try:
        if cmd in ("", "--json"):
            return fav_show(lib, cmd == "--json")
        if cmd == "add":
            return fav_add(lib, cfg, group, refs)
        if cmd == "remove":
            status = EXIT_OK
            for ref in argv[1:]:
                ident = fav_ref(lib, yt, ref)
                if not ident:
                    status = EXIT_FAIL
                    continue
                try:
                    lib.fav_remove(ident)
                except LibError as e:
                    log.err(f"{ref}: {e}")
                    status = EXIT_FAIL
            return status
        if cmd == "set":
            ident = fav_ref(lib, yt, argv[1])
            if not ident:
                return EXIT_FAIL
            for a in argv[2:]:
                what, eq, value = a.partition("=")
                if not eq:
                    sys.stderr.write(FAV_USAGE)
                    return EXIT_USAGE
                try:
                    lib.fav_set(ident, what, value)
                except LibError as e:
                    log.err(str(e))
                    return EXIT_FAIL
            return EXIT_OK
        if cmd == "import":
            return fav_import(lib, argv[1])
        sys.stdout.write(lib.fav_export(opml=argv[1:2] != ["--csv"]))
        return EXIT_OK
    except LibError as e:
        log.err(str(e))
        return EXIT_FAIL
    finally:
        lib.close()


HISTORY_USAGE = """\
Usage: vts history [-n N] [--json]
       vts history remove VIDEO...
       vts history clear
What you watched, most recent first, and where you
stopped (the next time starts there).
"""


def cmd_history(cfg, argv):
    cmd = argv[0] if argv else ""
    if cmd in ("--help", "-h"):
        sys.stdout.write(HISTORY_USAGE)
        return EXIT_OK
    if cmd in ("remove", "clear"):
        if (cmd == "remove" and len(argv) < 2) or (cmd == "clear" and
                                                   len(argv) != 1):
            print("Try 'vts history --help'.", file=sys.stderr)
            return EXIT_USAGE
        lib = open_library()
        if not lib:
            return EXIT_FAIL
        status = EXIT_OK
        with lib:
            if cmd == "clear":
                try:
                    lib.hist_clear()
                except LibError as e:
                    log.err(str(e))
                    status = EXIT_FAIL
            for a in argv[1:] if cmd == "remove" else ():
                ident = video.ref(a)
                try:
                    if not ident:
                        raise LibError("not a video")
                    lib.hist_remove(ident)
                except LibError:
                    log.err(f"{a}: not in the history")
                    status = EXIT_FAIL
        return status
    as_json = False
    limit = 50
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--json", "-j"):
            as_json = True
        elif a == "-n" and i + 1 < len(argv) and util.parse_long(
                argv[i + 1], 1, 100000) is not None:
            limit = util.parse_long(argv[i + 1], 1, 100000)
            i += 1
        else:
            print("Try 'vts history --help'.", file=sys.stderr)
            return EXIT_USAGE
        i += 1
    lib = open_library()
    if not lib:
        return EXIT_FAIL
    with lib:
        try:
            hist = lib.hist_list(limit)
        except LibError as e:
            log.err(str(e))
            return EXIT_FAIL
    if as_json:
        objs = []
        for w in hist:
            o = w.item.to_json()
            o.update(position=w.position, length=w.length, watched=w.watched,
                     when=w.when)
            objs.append(o)
        print_json(objs)
        return EXIT_OK
    for w in hist:
        at = "watched" if w.watched else util.format_time(w.position)
        out("\t".join((w.item.get("id"), w.item.get("title"),
                       w.item.get("channel"), at)))
    return EXIT_OK


LIST_USAGE = """\
Usage: vts list                        your playlists
       vts list show NAME [--json]     the videos of one
       vts list create NAME
       vts list delete NAME
       vts list rename NAME NEW
       vts list add NAME VIDEO...
       vts list remove NAME VIDEO...
       vts later [add|remove VIDEO...] Watch later
Playlists kept on your computer, Watch later among them.
"""


def list_videos(lib, cfg, name, add, refs):
    yt = None
    status = EXIT_OK
    for a in refs:
        ident = video.ref(a)
        if not ident:
            log.err(f"not a video id or link: {a}")
            status = EXIT_FAIL
            continue
        if not add:
            try:
                lib.list_remove(name, ident)
            except LibError as e:
                log.err(f"{a}: {e}")
                status = EXIT_FAIL
            continue
        # its title and channel, to show the list without the network
        if not yt:
            yt = Client(cfg)
        try:
            it = video.get(yt, ident).info
        except YouTubeError as e:
            log.warn(f"{ident}: {e}")
            it = Item("video", id=ident)
        try:
            lib.list_add(name, it)
        except LibError as e:
            if e.kind == EXISTS:
                out(f"{ident} is already in {name}")
            else:
                log.err(str(e))
                status = EXIT_FAIL
    return status


def list_run(cfg, argv):
    cmd = argv[0] if argv else ""
    n = len(argv)
    if cmd and not ((cmd == "show" and n in (2, 3)) or
                    (cmd in ("create", "delete") and n == 2) or
                    (cmd == "rename" and n == 3) or
                    (cmd in ("add", "remove") and n >= 3)):
        sys.stderr.write(LIST_USAGE)
        return EXIT_USAGE
    lib = open_library()
    if not lib:
        return EXIT_FAIL
    with lib:
        try:
            if not cmd:
                for pl in lib.lists():
                    out(f"{pl.name}\t{pl.count}")
                return EXIT_OK
            if cmd == "show":
                try:
                    page = lib.list_items(argv[1])
                except LibError as e:
                    log.err(f"{argv[1]}: {e}")
                    return EXIT_FAIL
                if n == 3 and argv[2] == "--json":
                    print_json(page.to_json())
                else:
                    print_items(page.items)
                return EXIT_OK
            if cmd == "create":
                lib.list_create(argv[1])
            elif cmd == "delete":
                lib.list_delete(argv[1])
            elif cmd == "rename":
                lib.list_rename(argv[1], argv[2])
            else:
                return list_videos(lib, cfg, argv[1], cmd == "add", argv[2:])
            return EXIT_OK
        except LibError as e:
            log.err(str(e))
            return EXIT_FAIL


def cmd_list(cfg, argv):
    if argv[:1] in (["--help"], ["-h"]):
        sys.stdout.write(LIST_USAGE)
        return EXIT_OK
    return list_run(cfg, argv)


def cmd_later(cfg, argv):
    if argv[:1] in (["--help"], ["-h"]):
        sys.stdout.write(LIST_USAGE)
        return EXIT_OK
    if not argv or argv == ["--json"]:
        return list_run(cfg, ["show", WATCH_LATER, *argv])
    if argv[0] not in ("add", "remove") or len(argv) < 2:
        sys.stderr.write(LIST_USAGE)
        return EXIT_USAGE
    # add|remove "Watch later" VIDEO...
    return list_run(cfg, [argv[0], WATCH_LATER, *argv[1:]])

# SPDX-License-Identifier: Apache-2.0
"""The commands: everything in the app also has a command, for scripts."""

import getopt
import json
import sys

from vts import log, rules, text, util

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
MAX_PAGES = 20


class Usage(Exception):
    """a bad command line: the message, then exit status 2"""

    def __init__(self, msg=None, cmd=None):
        super().__init__(msg)
        self.msg = msg
        self.cmd = cmd


def parse(cmd, argv, short, long):
    """the options and arguments of @argv (without the command's name)"""
    try:
        return getopt.gnu_getopt(argv, short, long)
    except getopt.GetoptError as e:
        print(f"vts {cmd}: {e.msg}", file=sys.stderr)
        raise Usage(None, cmd) from None


def try_help(cmd):
    print(f"Try 'vts {cmd} --help' for more information.", file=sys.stderr)


def pages_arg(cmd, v):
    n = util.parse_long(v, 1, MAX_PAGES)
    if n is None:
        print(f"vts {cmd}: --pages: 1 to {MAX_PAGES}", file=sys.stderr)
        raise Usage()
    return n


def out(s=""):
    sys.stdout.write(s + "\n")


def print_json(obj):
    out(json.dumps(obj, ensure_ascii=False, indent="\t"))


# --- results, as search prints them ------------------------------------------

def when(it):
    """ "11 months ago", or the date of a feed entry"""
    return it.f.get("published") or it.get("date")


def length_of(it):
    """ "53:44", "LIVE", "upcoming" or "" """
    if "live" in it.f:
        return "LIVE"
    if "upcoming" in it.f:
        return "upcoming"
    return it.get("duration")


def parts(*ss):
    """the non-empty @ss joined by " · " """
    return " · ".join(s for s in ss if s)


def print_tsv(it):
    who = it.get("handle") if it.kind == "channel" else it.get("channel")
    count = it.get("views")
    if it.kind == "channel":
        count = it.get("subscribers")
    elif it.kind == "playlist":
        count = it.get("videos")
    out("\t".join((it.kind, it.get("id"), it.get("title"), who,
                   length_of(it), count, when(it))))


def details(it):
    """the second line of a result: what it is, its length, its channel..."""
    head = f"{it.kind} {it.get('id')}"
    if it.kind == "channel":
        return parts(head, it["handle"], it["subscribers"])
    if it.kind == "playlist":
        return parts(head, it["videos"], it["channel"])
    if it.kind == "post":
        return parts(head, it["published"],
                     f"{it['likes']} likes" if it["likes"] else None,
                     f"{it['replies']} comments" if it["replies"] else None,
                     f"video {it['video']}" if it["video"] else None)
    w = None
    if not it.f.get("published") and it.f.get("date"):
        w = util.ago(it.f["date"])
    return parts(head, length_of(it), it["channel"], it["views"],
                 w or when(it))


def print_tty(it, cols):
    if it.kind == "post":
        # all its text, as comments
        out(text.fit(details(it), cols))
        print_indented(it.get("text") or it.get("title"), 2)
        out()
        return
    out(text.fit(it.get("title"), cols))
    out("  " + text.fit(details(it), cols - 2))


def print_items(items, cols=None):
    from vts import platform
    if cols is None:
        cols = platform.term_cols()
    for it in items:
        if not cols:
            print_tsv(it)
        else:
            print_tty(it, cols)


def print_indented(s, indent):
    lines = s.split("\n") if s else []
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        out((" " * indent if line else "") + line)


__all__ = ["Usage", "parse", "rules"]


def error(msg):
    log.err(msg)
    return EXIT_FAIL

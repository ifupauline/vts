# SPDX-License-Identifier: Apache-2.0
"""
Where the information is in YouTube's answers, as described by a rules
file (youtube.json): which renderers are results, and the paths to each
of their fields. YouTube changes its answers often; the rules change
with them, without a new program.

A path is a list of steps separated by dots: a key, an index in a list
(-1 for the last element), * for any child (the first that matches),
{a/b} to go on only from a node that has a.b, and a final =VALUE for a
string that must be VALUE.
"""

import json

VERSION = 1
PATH_MAX = 512
SEG_MAX = 127
# how deep results are looked for: YouTube's answers go about 40 deep
WALK_DEPTH = 200

KINDS = ("video", "short", "channel", "playlist", "comment", "post")
FIELDS = ("id", "title", "channel", "channel_id", "duration", "views",
          "published", "thumbnail", "description", "handle", "subscribers",
          "videos", "live", "upcoming", "date", "likes", "text", "replies",
          "replies_token", "pinned", "creator", "start_ms", "video",
          "video_title", "poll")
# texts that keep their line breaks
MULTILINE = ("description", "text")
# fields that are only there or not
FLAGS = ("live", "upcoming", "pinned", "creator")

_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                      "0123456789-_")


def id_chars(s):
    return all(c in _ID_CHARS for c in s)


def valid_video_id(s):
    return isinstance(s, str) and len(s) == 11 and id_chars(s)


def valid_channel_id(s):
    return (isinstance(s, str) and len(s) == 24 and s.startswith("UC") and
            id_chars(s))


def valid_playlist_id(s):
    return isinstance(s, str) and 2 <= len(s) <= 64 and id_chars(s)


def valid_comment_id(s):
    """ "UgzYRFhy_aGTiYqYHmh4AaABAg", a reply adds ".8xyz..." """
    return (isinstance(s, str) and 4 <= len(s) <= 128 and
            all(c in _ID_CHARS or c == "." for c in s))


class Item:
    """a result: a video, a Short, a channel, a playlist or a comment"""

    __slots__ = ("kind", "f", "data")

    def __init__(self, kind, **fields):
        self.kind = kind
        self.f = {k: v for k, v in fields.items() if v is not None}
        self.data = None  # what the app keeps with it

    def get(self, field, default=""):
        return self.f.get(field, default)

    def __getitem__(self, field):
        return self.f.get(field)

    def __repr__(self):
        return f"Item({self.kind!r}, {self.f!r})"

    def to_json(self):
        o = {"kind": self.kind}
        for k in FIELDS:
            if k in self.f:
                o[k] = True if k in FLAGS else self.f[k]
        return o


class Page:
    """results, with the token of the next page"""

    def __init__(self):
        self.items = []
        self.continuation = None
        self.estimated = None
        self._ids = set()

    def add(self, item):
        if item.f["id"] in self._ids:
            return False
        self._ids.add(item.f["id"])
        self.items.append(item)
        return True

    def __len__(self):
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def to_json(self):
        return [it.to_json() for it in self.items]


class BadRules(ValueError):
    pass


# --- checking a rules file ---------------------------------------------------

def _string_array(a, may_be_missing):
    if a is None:
        return may_be_missing
    return isinstance(a, list) and all(
        isinstance(c, str) and len(c.encode()) <= PATH_MAX for c in a)


def _get(doc, dotted):
    node = doc
    for seg in dotted.split("."):
        node = step(node, seg)
        if node is None:
            return None
    return node


def _fields_ok(fields):
    if fields is None:
        return True
    return isinstance(fields, dict) and all(
        _string_array(v, False) for v in fields.values())


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class Rules:
    def __init__(self, text):
        """the rules in @text (str or bytes); BadRules with the reason"""
        try:
            if isinstance(text, bytes):
                text = text.decode("utf-8", "replace")
            doc = json.loads(text)
        except (ValueError, RecursionError):
            raise BadRules("not valid JSON") from None
        if not isinstance(doc, dict):
            raise BadRules(f"version must be 1 to {VERSION}")
        v = doc.get("version")
        if not _is_number(v) or v < 1 or v > VERSION or v != int(v):
            raise BadRules(f"version must be 1 to {VERSION}")
        items = doc.get("items")
        if (not isinstance(items, list) or
                not _string_array(doc.get("skip"), True) or
                not _string_array(doc.get("continuation"), True) or
                not _string_array(doc.get("estimated_results"), True) or
                not isinstance(doc.get("continuation_renderer"), str)):
            raise BadRules("needs items, continuation_renderer and lists of "
                           "strings")
        for n, it in enumerate(items):
            _check_item(it, n)
        _check_sections(doc)
        rev = doc.get("revision", 0)
        if "revision" in doc and (not _is_number(rev) or rev < 0 or
                                  rev > 9999999999 or rev != int(rev)):
            raise BadRules("revision must be a number like 2026111201")
        self.doc = doc
        self.version = int(v)
        self.revision = int(rev)
        self._skip = set(doc.get("skip") or ())
        self._cont = doc["continuation_renderer"]
        self._by_renderer = {}
        for it in items:
            # a kind of result a newer vts knows: left for it
            if it["kind"] in KINDS:
                self._by_renderer.setdefault(it["renderer"], []).append(it)

    def section(self, dotted):
        return _get(self.doc, dotted)

    def first(self, dotted, node):
        """the first text of the paths of section @dotted under @node"""
        paths = self.section(dotted)
        return first_text(node, paths) if paths else None

    # --- results ----------------------------------------------------------

    def page(self, doc, page):
        """the results in the answer @doc, added to @page"""
        # an answer without a token of its own is the last page
        page.continuation = None
        self._walk(doc, page)
        if not page.estimated:
            page.estimated = first_text(doc, self.doc.get("estimated_results"))
        return page

    def page_under(self, dotted, doc, page):
        """the results under the first root of section @dotted"""
        page.continuation = None
        for p in self.section(dotted) or ():
            root = path(doc, p)
            if root is not None:
                self._walk(root, page)
                break
        return page

    def _walk(self, node, page, depth=0):
        if depth > WALK_DEPTH:
            return
        children = node.items() if isinstance(node, dict) else (
            (None, c) for c in node)
        for key, c in children:
            if not isinstance(c, (dict, list)):
                continue
            if key is not None:
                if key in self._skip:
                    continue
                if key == self._cont:
                    t = first_text(c, self.doc.get("continuation"))
                    if t:
                        page.continuation = t
                    continue
                if isinstance(c, dict) and self._try_rules(key, c, page):
                    continue
            self._walk(c, page, depth + 1)

    def _try_rules(self, key, node, page):
        for rule in self._by_renderer.get(key, ()):
            when = rule.get("when")
            if when and path(node, when) is None:
                continue
            item = Item(rule["kind"])
            fill(rule["fields"], node, item)
            if check_item(item):
                page.add(item)
            return True
        return False

    # --- comments ---------------------------------------------------------

    def comments(self, doc, page):
        c = self.section("comments")
        if not isinstance(c, dict):
            raise BadRules("no comments section")
        entities = []
        name = c.get("entity")
        if name:
            _each(doc, name, entities)
        keys = c.get("entity_key")
        by_key = {}
        for e in entities:
            k = first_text(e, keys)
            if k and k not in by_key:
                by_key[k] = e
        page.continuation = None
        self._walk_comments(doc, c, by_key, page)
        if not page.estimated:
            page.estimated = self.first("comments.count", doc)
        return page

    def _walk_comments(self, node, c, by_key, page, depth=0):
        if depth > WALK_DEPTH:
            return
        thread = c.get("thread")
        reply = c.get("reply")
        children = node.items() if isinstance(node, dict) else (
            (None, x) for x in node)
        for key, x in children:
            if not isinstance(x, (dict, list)):
                continue
            if key is not None and thread and key == thread:
                self._add_comment(c, by_key, x, x, page)
            elif key is not None and reply and key == reply:
                self._add_comment(c, by_key, x, None, page)
            elif key is not None and key == self._cont:
                t = first_text(x, self.doc.get("continuation"))
                if t:
                    page.continuation = t
            elif key != "frameworkUpdates":
                self._walk_comments(x, c, by_key, page, depth + 1)

    def _add_comment(self, c, by_key, node, thread, page):
        key = first_text(node, c.get("key"))
        entity = by_key.get(key) if key else None
        if entity is None:
            return
        item = Item("comment")
        fill(c.get("fields"), entity, item)
        if thread is not None:
            fill(c.get("thread_fields"), thread, item)
        if check_item(item):
            page.add(item)


def _each(node, key, out, depth=0):
    """every value of a member @key under @node (not looking inside them)"""
    if depth > WALK_DEPTH:
        return
    if isinstance(node, dict):
        for k, x in node.items():
            if k == key:
                out.append(x)
            elif isinstance(x, (dict, list)):
                _each(x, key, out, depth + 1)
    elif isinstance(node, list):
        for x in node:
            if isinstance(x, (dict, list)):
                _each(x, key, out, depth + 1)


def _check_item(it, n):
    if not isinstance(it, dict):
        raise BadRules(f"item {n}: needs renderer, kind and fields")
    fields = it.get("fields")
    when = it.get("when")
    if (not isinstance(it.get("renderer"), str) or
            not isinstance(it.get("kind"), str) or
            not isinstance(fields, dict) or
            (when is not None and not isinstance(when, str))):
        raise BadRules(f"item {n}: needs renderer, kind and fields")
    # fields this program doesn't know are left for newer programs
    for name, paths in fields.items():
        if not _string_array(paths, False):
            raise BadRules(f"item {n}: field {name}: expected paths")
    if "id" not in fields:
        raise BadRules(f"item {n}: no id field")


def _check_sections(doc):
    """the sections for pages other than results, all optional"""
    for s in ("watch.fields", "watch.chapters.fields", "comments.fields",
              "comments.thread_fields", "channel.fields", "playlist.fields"):
        if not _fields_ok(_get(doc, s)):
            raise BadRules(f"{s}: expected field: [paths]")
    for s in ("watch.chapters.lists", "watch.related", "watch.comments",
              "comments.key", "comments.entity_key", "comments.count",
              "comments.sort.top", "comments.sort.newest", "channel.results",
              "channel.resolve", "playlist.results"):
        if not _string_array(_get(doc, s), True):
            raise BadRules(f"{s}: expected paths")
    tabs = _get(doc, "channel.tabs")
    if tabs is not None and not (isinstance(tabs, dict) and all(
            isinstance(v, str) for v in tabs.values())):
        raise BadRules("channel.tabs: expected tab: params")
    for s in ("comments.thread", "comments.reply", "comments.entity"):
        v = _get(doc, s)
        if v is not None and not isinstance(v, str):
            raise BadRules(f"{s}: expected a name")


# --- paths -------------------------------------------------------------------

def step(node, seg):
    """the child @seg of @node: a key, or an index in a list"""
    if isinstance(node, list):
        if not 1 <= len(seg) <= 9 or not (seg.isascii() and seg.isdigit()):
            return None
        i = int(seg)
        return node[i] if i < len(node) else None
    if isinstance(node, dict):
        return node.get(seg)
    return None


def _eval(node, segs, i, want):
    while True:
        if node is None:
            return None
        if i == len(segs):
            if want is None:
                return node
            return node if isinstance(node, str) and node == want else None
        seg = segs[i]
        i += 1
        if len(seg) > SEG_MAX:
            return None
        if seg == "*":
            if isinstance(node, dict):
                children = node.values()
            elif isinstance(node, list):
                children = node
            else:
                return None
            for c in children:
                found = _eval(c, segs, i, want)
                if found is not None:
                    return found
            return None
        # {a/b/c}: go on from this node only if it has a.b.c
        if len(seg) >= 2 and seg[0] == "{" and seg[-1] == "}":
            cond = seg[1:-1].replace("/", ".")
            if _eval(node, cond.split("."), 0, None) is None:
                return None
            continue
        if seg == "-1":
            if not isinstance(node, list) or not node:
                return None
            node = node[-1]
            continue
        node = step(node, seg)


def path(node, p):
    """what the path @p leads to under @node, or None"""
    if len(p.encode()) > PATH_MAX:
        return None
    p, eq, want = p.partition("=")
    segs = p.split(".") if p else []
    # as "a." says: a, nothing more
    if p.endswith("."):
        segs.pop()
    return _eval(node, segs, 0, want if eq else None)


def _num(v):
    s = format(float(v), ".15g")
    return s


def text(node, multiline=False):
    """the text of @node: a string, a number, true, simpleText, runs"""
    from vts.text import clean
    if node is None:
        return ""
    if isinstance(node, str):
        return clean(node, multiline)
    if isinstance(node, bool):
        return "1" if node else ""
    if isinstance(node, (int, float)):
        return _num(node)
    if isinstance(node, dict):
        s = node.get("simpleText")
        if not isinstance(s, str):
            s = node.get("content")
        if isinstance(s, str):
            return clean(s, multiline)
        runs = node.get("runs")
        if isinstance(runs, list):
            return "".join(clean(r["text"], multiline) for r in runs
                           if isinstance(r, dict) and
                           isinstance(r.get("text"), str))
    return ""


def first_text(node, paths, multiline=False):
    """the first non-empty text from @paths under @node, or None"""
    for p in paths or ():
        t = text(path(node, p), multiline)
        if t:
            return t
    return None


def fill(fields, node, item):
    """the fields of @item from the paths of @fields under @node"""
    if not fields:
        return
    for name in FIELDS:
        paths = fields.get(name)
        if paths is None:
            continue
        t = first_text(node, paths, name in MULTILINE)
        if t is None:
            item.f.pop(name, None)
        else:
            item.f[name] = t


def check_item(item):
    """whether @item is a result to keep (and what to drop from it)"""
    f = item.f
    ident = f.get("id")
    k = item.kind
    if k in ("video", "short"):
        ok = valid_video_id(ident)
    elif k == "channel":
        ok = valid_channel_id(ident)
    elif k in ("comment", "post"):
        ok = valid_comment_id(ident)
    else:
        ok = valid_playlist_id(ident)
    if not ok:
        return False
    if "channel_id" in f and not valid_channel_id(f["channel_id"]):
        del f["channel_id"]
    if "video" in f and not valid_video_id(f["video"]):
        del f["video"]
    # "//yt3.ggpht.com/..." means https; anything else but https goes
    thumb = f.get("thumbnail")
    if thumb is not None:
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        if not thumb.startswith("https://") or any(c in thumb
                                                   for c in " \"'<>\\"):
            del f["thumbnail"]
        else:
            f["thumbnail"] = thumb
    # "/@kurzgesagt"
    h = f.get("handle")
    if h is not None:
        if h.startswith("/"):
            h = h[1:]
        if (not h.startswith("@") or len(h.encode()) > 64 or len(h) < 2 or
                not id_chars(h[1:])):
            del f["handle"]
        else:
            f["handle"] = h
    return True


def builtin_text():
    """the rules that come with this version of vts"""
    from importlib import resources
    return resources.files("vts").joinpath("youtube.json").read_bytes()


def builtin():
    return Rules(builtin_text())

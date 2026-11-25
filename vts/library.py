# SPDX-License-Identifier: Apache-2.0
"""
Everything vts keeps about you, on your computer only, in one SQLite file
(~/.local/share/vts/vts.db): favourite channels, the videos of theirs
already seen, the history with where you stopped, Watch later and your
own playlists, your recent searches and downloads. The full screen app
and the notifier may use it at the same time.
"""

import csv
import io
import os
import sqlite3
import time
import xml.parsers.expat

from vts import platform
from vts import sites
from vts.rules import (KINDS, Item, Page, valid_channel_id, valid_video_id)
from vts.text import clean

VERSION = 2  # of the database layout
WATCH_LATER = "Watch later"
BUSY_TIMEOUT = 5.0
IMPORT_MAX = 16 * 1024 * 1024
SEARCHES_KEPT = 50

# kinds of failure
NOT_FOUND = "not-found"
EXISTS = "exists"
INVALID = "invalid"
IO = "io"
REFUSED = "refused"
BAD_FILE = "bad-file"


class LibError(Exception):
    def __init__(self, message, kind=IO):
        super().__init__(message)
        self.kind = kind


SCHEMA_1 = f"""
CREATE TABLE favorites (
 channel_id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 handle TEXT,
 grp TEXT NOT NULL DEFAULT '',
 notify INTEGER NOT NULL DEFAULT 1,
 shorts INTEGER NOT NULL DEFAULT 1,
 added INTEGER NOT NULL);
CREATE TABLE seen (
 video_id TEXT PRIMARY KEY,
 channel_id TEXT NOT NULL,
 kind TEXT NOT NULL,
 title TEXT,
 published TEXT,
 first_seen INTEGER NOT NULL);
CREATE INDEX seen_channel ON seen (channel_id);
CREATE TABLE history (
 video_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL,
 title TEXT,
 channel TEXT,
 channel_id TEXT,
 duration TEXT,
 position INTEGER NOT NULL DEFAULT 0,
 length INTEGER NOT NULL DEFAULT 0,
 watched INTEGER NOT NULL DEFAULT 0,
 last INTEGER NOT NULL,
 seq INTEGER NOT NULL);
CREATE INDEX history_seq ON history (seq);
CREATE TABLE lists (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE,
 created INTEGER NOT NULL);
CREATE TABLE list_items (
 list INTEGER NOT NULL REFERENCES lists (id) ON DELETE CASCADE,
 pos INTEGER NOT NULL,
 video_id TEXT NOT NULL,
 kind TEXT NOT NULL,
 title TEXT,
 channel TEXT,
 channel_id TEXT,
 duration TEXT,
 added INTEGER NOT NULL,
 PRIMARY KEY (list, video_id));
INSERT INTO lists (name, created) VALUES ('{WATCH_LATER}',
 strftime('%s', 'now'));
"""

# version 2: the recent searches and the downloads
SCHEMA_2 = """
CREATE TABLE searches (
 query TEXT PRIMARY KEY,
 last INTEGER NOT NULL);
CREATE TABLE downloads (
 id INTEGER PRIMARY KEY,
 video_id TEXT NOT NULL,
 kind TEXT NOT NULL,
 title TEXT,
 channel TEXT,
 path TEXT NOT NULL,
 audio INTEGER NOT NULL DEFAULT 0,
 finished INTEGER NOT NULL);
"""


def path():
    """the usual file, ~/.local/share/vts/vts.db (its folder is made)"""
    d = platform.data_dir()
    if not d:
        raise LibError("no home folder", NOT_FOUND)
    platform.mkdirs(d)
    return os.path.join(d, "vts.db")


def _now():
    return int(time.time())


class Fav:
    __slots__ = ("id", "name", "handle", "group", "notify", "shorts", "added")

    def __init__(self, row):
        (self.id, self.name, self.handle, self.group, notify, shorts,
         self.added) = row
        self.notify = bool(notify)
        self.shorts = bool(shorts)

    def to_json(self):
        o = {"id": self.id, "name": self.name}
        if self.handle:
            o["handle"] = self.handle
        o.update(group=self.group, notify=self.notify, shorts=self.shorts)
        return o


class Watch:
    __slots__ = ("item", "position", "length", "watched", "when")

    def __init__(self, item, position, length, watched, when):
        self.item = item  # id, title, channel, channel_id, duration
        self.position = position  # seconds, where to resume
        self.length = length  # seconds, 0 if unknown
        self.watched = watched  # seen to the end (90 %)
        self.when = when  # last watched, seconds since 1970


class PlayList:
    __slots__ = ("name", "count", "created")

    def __init__(self, name, count, created):
        self.name = name
        self.count = count
        self.created = created


class Download:
    __slots__ = ("id", "item", "path", "audio", "finished")

    def __init__(self, row):
        (self.id, vid, kind, title, channel, self.path, audio,
         self.finished) = row
        self.item = Item(kind if kind in KINDS else "video", id=vid,
                         title=title, channel=channel)
        self.audio = bool(audio)


def _item(row):
    vid, kind, title, channel, channel_id, duration = row
    return Item(kind if kind in KINDS else "video", id=vid, title=title,
                channel=channel, channel_id=channel_id, duration=duration)


FAV_COLUMNS = "channel_id, name, handle, grp, notify, shorts, added"


class Library:
    def __init__(self, file=None):
        """open (and make or upgrade) the library; LibError"""
        file = file or path()
        try:
            # made private before SQLite opens it with the default mode
            if not os.path.exists(file):
                os.close(os.open(file, os.O_WRONLY | os.O_CREAT, 0o600))
            self.db = sqlite3.connect(file, timeout=BUSY_TIMEOUT,
                                      isolation_level=None,
                                      check_same_thread=False)
            self.db.execute("PRAGMA journal_mode = WAL")
            self.db.execute("PRAGMA synchronous = NORMAL")
            self.db.execute("PRAGMA foreign_keys = ON")
            self._upgrade()
        except (sqlite3.Error, OSError) as e:
            raise LibError(f"{file}: {e}") from None
        self.file = file

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _upgrade(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > VERSION:
            raise LibError("the library was made by a newer vts", REFUSED)
        if version == VERSION:
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._script(SCHEMA_1)
            if version < 2:
                self._script(SCHEMA_2)
            self.db.execute(f"PRAGMA user_version = {VERSION}")
            self.db.execute("COMMIT")
        except sqlite3.Error:
            self.db.execute("ROLLBACK")
            raise

    def _script(self, sql):
        # not executescript(), which would end the transaction
        for stmt in sql.split(";\n"):
            if stmt.strip():
                self.db.execute(stmt)

    def _run(self, sql, args=()):
        try:
            return self.db.execute(sql, args)
        except sqlite3.Error as e:
            raise LibError(str(e)) from None

    # --- favourites -------------------------------------------------------

    def fav_add(self, ident, name, handle=None, group=None):
        """@ident: a channel of any site (sites.py)"""
        if not sites.valid_channel_id(ident) or not name:
            raise LibError("not a channel", INVALID)
        try:
            self.db.execute(
                "INSERT INTO favorites (channel_id, name, handle, grp, added)"
                " VALUES (?, ?, ?, ?, ?)",
                (ident, name, handle, group or "", _now()))
        except sqlite3.IntegrityError:
            raise LibError("already a favourite", EXISTS) from None
        except sqlite3.Error as e:
            raise LibError(str(e)) from None

    def fav_remove(self, ident):
        if not self._run("DELETE FROM favorites WHERE channel_id = ?",
                         (ident,)).rowcount:
            raise LibError("not a favourite", NOT_FOUND)

    def fav_set(self, ident, what, value):
        """@what: notify, shorts (yes/no) or group (a name, "" for none)"""
        from vts.util import parse_bool
        if what == "group":
            v = value
            col = "grp"
        elif what in ("notify", "shorts") and parse_bool(value) is not None:
            v = int(parse_bool(value))
            col = what
        else:
            raise LibError("expected notify=yes|no, shorts=yes|no or "
                           "group=NAME", INVALID)
        if not self._run(f"UPDATE favorites SET {col} = ? WHERE channel_id"
                         " = ?", (v, ident)).rowcount:
            raise LibError("not a favourite", NOT_FOUND)

    def favs(self, site="youtube"):
        """those of @site (None: all), by group then name"""
        where, args = sites.sql_filter(site, "channel_id")
        return [Fav(r) for r in self._run(
            f"SELECT {FAV_COLUMNS} FROM favorites WHERE {where}"
            " ORDER BY grp, name COLLATE NOCASE", args)]

    def fav(self, ident):
        """one of them, or None"""
        r = self._run(f"SELECT {FAV_COLUMNS} FROM favorites WHERE "
                      "channel_id = ?", (ident,)).fetchone()
        return Fav(r) if r else None

    # --- import and export -----------------------------------------------

    def _import_one(self, ident, name, added):
        if not valid_channel_id(ident):
            return
        name = clean(name)
        try:
            self.fav_add(ident, name or ident)
            added[0] += 1
        except LibError as e:
            if e.kind != EXISTS:
                raise

    def fav_import(self, data):
        """
        add the channels of a subscriptions export: Google Takeout's
        subscriptions.csv ("Channel Id,Channel Url,Channel Title") or an
        OPML file of YouTube feeds; how many were new
        """
        if len(data) > IMPORT_MAX or b"\0" in data:
            raise LibError("not a subscriptions file", BAD_FILE)
        # a byte order mark, if any
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        added = [0]
        self._run("BEGIN")
        try:
            if data.startswith(b"Channel Id,"):
                self._import_csv(data.decode("utf-8", "replace"), added)
            elif b"<opml" in data:
                self._import_opml(data, added)
            else:
                raise LibError("bad", BAD_FILE)
        except LibError as e:
            self.db.execute("ROLLBACK")
            if e.kind == BAD_FILE:
                raise LibError("not a subscriptions file (Takeout CSV or "
                               "OPML)", BAD_FILE) from None
            raise
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        self._run("COMMIT")
        return added[0]

    def _import_csv(self, s, added):
        lines = s.split("\n", 1)
        rows = csv.reader(io.StringIO(lines[1] if len(lines) > 1 else ""))
        try:
            for row in rows:
                if row and row[0]:
                    self._import_one(row[0], row[2] if len(row) > 2 else "",
                                     added)
        except csv.Error:
            raise LibError("bad", BAD_FILE) from None

    def _import_opml(self, data, added):
        state = {"opml": False}

        def start(name, atts):
            if name == "opml":
                state["opml"] = True
            if name != "outline":
                return
            url = atts.get("xmlUrl", "")
            i = url.find("channel_id=")
            if i < 0:
                return
            self._import_one(url[i + 11:i + 11 + 24],
                             atts.get("title") or atts.get("text") or "",
                             added)

        def doctype(*args):
            raise LibError("bad", BAD_FILE)

        xp = xml.parsers.expat.ParserCreate("UTF-8")
        xp.StartElementHandler = start
        xp.StartDoctypeDeclHandler = doctype
        xp.EntityDeclHandler = doctype
        try:
            xp.Parse(data, True)
        except xml.parsers.expat.ExpatError:
            raise LibError("bad", BAD_FILE) from None
        if not state["opml"]:
            raise LibError("bad", BAD_FILE)

    def fav_export(self, opml=True):
        """the favourites as OPML (for feed readers) or as Takeout's CSV"""
        favs = self.favs()
        if not opml:
            out = ["Channel Id,Channel Url,Channel Title\n"]
            for f in favs:
                name = f.name
                if any(c in name for c in ",\"\r\n"):
                    name = '"' + name.replace('"', '""') + '"'
                out.append(f"{f.id},http://www.youtube.com/channel/{f.id},"
                           f"{name}\n")
            return "".join(out)

        def esc(s):
            return (s.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

        out = ['<?xml version="1.0" encoding="UTF-8"?>\n'
               '<opml version="1.1">\n'
               "<head><title>vts favourites</title></head>\n"
               "<body>\n"]
        group = None
        for f in favs:
            # one outline per group, around its channels
            if group is None or group != f.group:
                if group is not None:
                    out.append("</outline>\n")
                out.append(f'<outline text="{esc(f.group or "YouTube")}">\n')
                group = f.group
            out.append(f'  <outline type="rss" text="{esc(f.name)}" '
                       'xmlUrl="https://www.youtube.com/feeds/videos.xml?'
                       f'channel_id={f.id}" '
                       f'htmlUrl="https://www.youtube.com/channel/{f.id}"/>\n')
        out.append("</outline>\n</body>\n</opml>\n" if group is not None
                   else "</body>\n</opml>\n")
        return "".join(out)

    # --- videos of favourites already seen, for notifications -------------

    def seen_add(self, it):
        """note the feed entry @it as seen: whether it is new"""
        if not valid_video_id(it["id"]) or not valid_channel_id(
                it["channel_id"]):
            raise LibError("not a video of a channel", INVALID)
        return self._run(
            "INSERT OR IGNORE INTO seen (video_id, channel_id, kind, title,"
            " published, first_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (it["id"], it["channel_id"], it.kind, it["title"], it["date"],
             _now())).rowcount == 1

    def seen_any(self, channel_id):
        """whether the channel has anything seen yet (its first check)"""
        return self._run("SELECT 1 FROM seen WHERE channel_id = ? LIMIT 1",
                         (channel_id,)).fetchone() is not None

    def seen_recent(self, limit=0):
        """the latest uploads of the favourites, newest first: Home"""
        page = Page()
        for vid, kind, title, name, cid, published in self._run(
                "SELECT s.video_id, s.kind, s.title, f.name, s.channel_id,"
                " s.published FROM seen s JOIN favorites f"
                " ON f.channel_id = s.channel_id"
                " WHERE f.shorts OR s.kind != 'short'"
                " ORDER BY s.published DESC LIMIT ?",
                (limit if limit > 0 else -1,)):
            page.add(Item(kind if kind in KINDS else "video", id=vid,
                          title=title, channel=name, channel_id=cid,
                          date=published))
        return page

    def seen_prune(self, days):
        """forget what is older than @days for channels no longer kept"""
        self._run("DELETE FROM seen WHERE first_seen < ? AND channel_id"
                  " NOT IN (SELECT channel_id FROM favorites)",
                  (_now() - days * 86400,))

    # --- history ----------------------------------------------------------

    def hist_record(self, it, position, length):
        """
        @it was watched up to @position of @length seconds; at 90 % it
        counts as watched and the next time starts from the beginning
        """
        position = int(position)
        length = int(length)
        if not sites.valid_video_id(it["id"]) or position < 0 or length < 0:
            raise LibError("not a video", INVALID)
        watched = int(length > 0 and position >= length * 9 // 10)
        self._run(
            "INSERT INTO history (video_id, kind, title, channel,"
            " channel_id, duration, position, length, watched, last, seq)"
            " VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,"
            " (SELECT coalesce(max(seq), 0) + 1 FROM history))"
            " ON CONFLICT (video_id) DO UPDATE SET"
            " title = coalesce(?3, title),"
            " channel = coalesce(?4, channel),"
            " channel_id = coalesce(?5, channel_id),"
            " duration = coalesce(?6, duration),"
            " position = ?7, length = max(?8, length),"
            " watched = ?9, last = ?10,"
            " seq = (SELECT max(seq) + 1 FROM history)",
            (it["id"], it.kind, it["title"], it["channel"], it["channel_id"],
             it["duration"], position, length, watched, _now()))

    def hist_resume(self, ident):
        """where to resume @ident (0 if never watched or watched to the end)"""
        try:
            r = self.db.execute("SELECT position, watched FROM history WHERE "
                                "video_id = ?", (ident,)).fetchone()
        except sqlite3.Error:
            return 0
        return r[0] if r and not r[1] else 0

    def hist_list(self, limit=0, site=None):
        """the history (of @site, None: all), most recent first"""
        where, args = sites.sql_filter(site, "video_id")
        return [Watch(_item(r[:6]), r[6], r[7], bool(r[8]), r[9])
                for r in self._run(
                    "SELECT video_id, kind, title, channel, channel_id,"
                    " duration, position, length, watched, last FROM history"
                    f" WHERE {where} ORDER BY seq DESC LIMIT ?",
                    args + (limit if limit > 0 else -1,))]

    def hist_marks(self, ids):
        """{video id: (position, length, watched)} of those in the history"""
        out = {}
        ids = [i for i in ids if i]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            for vid, pos, length, watched in self._run(
                    "SELECT video_id, position, length, watched FROM history"
                    f" WHERE video_id IN ({q})", chunk):
                out[vid] = (pos, length, bool(watched))
        return out

    def hist_remove(self, ident):
        if not self._run("DELETE FROM history WHERE video_id = ?",
                         (ident,)).rowcount:
            raise LibError("not in the history", NOT_FOUND)

    def hist_clear(self):
        self._run("DELETE FROM history")

    # --- your playlists, Watch later among them --------------------------

    @staticmethod
    def _name_ok(name):
        return 0 < len(name.encode()) <= 200 and not any(
            ord(c) < 0x20 or ord(c) == 0x7F for c in name)

    def list_create(self, name):
        if not self._name_ok(name):
            raise LibError("not a playlist name", INVALID)
        if not self._run("INSERT OR IGNORE INTO lists (name, created) "
                         "VALUES (?, ?)", (name, _now())).rowcount:
            raise LibError("a playlist of that name exists", EXISTS)

    def list_delete(self, name):
        """Watch later can be emptied but not deleted"""
        if name == WATCH_LATER:
            raise LibError("Watch later can't be deleted", REFUSED)
        if not self._run("DELETE FROM lists WHERE name = ?",
                         (name,)).rowcount:
            raise LibError("no such playlist", NOT_FOUND)

    def list_rename(self, name, to):
        if name == WATCH_LATER:
            raise LibError("Watch later can't be renamed", REFUSED)
        if not self._name_ok(to):
            raise LibError("not a playlist name", INVALID)
        if not self._run("UPDATE OR IGNORE lists SET name = ? WHERE name = ?",
                         (to, name)).rowcount:
            raise LibError("no such playlist, or the new name is taken",
                           NOT_FOUND)

    def lists(self):
        """Watch later first, then by name"""
        return [PlayList(n, count, created) for n, created, count in
                self._run(
                    "SELECT name, created, (SELECT count(*) FROM list_items"
                    " WHERE list = lists.id) FROM lists ORDER BY name != ?,"
                    " name COLLATE NOCASE", (WATCH_LATER,))]

    def _list_id(self, name):
        r = self._run("SELECT id FROM lists WHERE name = ?",
                      (name,)).fetchone()
        if not r:
            raise LibError("no such playlist", NOT_FOUND)
        return r[0]

    def list_add(self, name, it):
        """@it at the end of the playlist @name; EXISTS if already in it"""
        lid = self._list_id(name)
        if not sites.valid_video_id(it["id"]):
            raise LibError("not a video", INVALID)
        if not self._run(
                "INSERT OR IGNORE INTO list_items (list, pos, video_id,"
                " kind, title, channel, channel_id, duration, added)"
                " VALUES (?1, (SELECT coalesce(max(pos), 0) + 1 FROM"
                " list_items WHERE list = ?1), ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                (lid, it["id"], it.kind, it["title"], it["channel"],
                 it["channel_id"], it["duration"], _now())).rowcount:
            raise LibError("already in that playlist", EXISTS)

    def list_remove(self, name, ident):
        lid = self._list_id(name)
        if not self._run("DELETE FROM list_items WHERE list = ? AND "
                         "video_id = ?", (lid, ident)).rowcount:
            raise LibError("not in that playlist", NOT_FOUND)

    def list_has(self, name, ident):
        try:
            lid = self._list_id(name)
        except LibError:
            return False
        return self._run("SELECT 1 FROM list_items WHERE list = ? AND "
                         "video_id = ?", (lid, ident)).fetchone() is not None

    def list_items(self, name):
        """the videos of @name, in order"""
        lid = self._list_id(name)
        page = Page()
        for r in self._run("SELECT video_id, kind, title, channel, "
                           "channel_id, duration FROM list_items WHERE "
                           "list = ? ORDER BY pos", (lid,)):
            page.add(_item(r))
        return page

    # --- recent searches --------------------------------------------------

    def search_add(self, query):
        query = query.strip()
        if not query or len(query) > 500:
            return
        # in order of use: a counter, as the clock can be too coarse
        self._run("INSERT INTO searches (query, last) VALUES (?1, (SELECT "
                  "coalesce(max(last), 0) + 1 FROM searches)) ON CONFLICT "
                  "(query) DO UPDATE SET last = (SELECT max(last) + 1 FROM "
                  "searches)", (query,))
        self._run("DELETE FROM searches WHERE query NOT IN (SELECT query "
                  "FROM searches ORDER BY last DESC LIMIT ?)",
                  (SEARCHES_KEPT,))

    def searches(self, prefix="", limit=10):
        """the recent searches starting with @prefix, latest first"""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace(
            "_", "\\_") + "%"
        return [q for (q,) in self._run(
            "SELECT query FROM searches WHERE query LIKE ? ESCAPE '\\' "
            "ORDER BY last DESC LIMIT ?", (like, limit))]

    def search_remove(self, query):
        self._run("DELETE FROM searches WHERE query = ?", (query,))

    def searches_clear(self):
        self._run("DELETE FROM searches")

    # --- downloads --------------------------------------------------------

    def download_add(self, it, file, audio):
        self._run("INSERT INTO downloads (video_id, kind, title, channel, "
                  "path, audio, finished) VALUES (?, ?, ?, ?, ?, ?, ?)",
                  (it["id"], it.kind, it["title"], it["channel"], file,
                   int(audio), _now()))

    def downloads(self, limit=200):
        """the finished downloads, latest first"""
        return [Download(r) for r in self._run(
            "SELECT id, video_id, kind, title, channel, path, audio, "
            "finished FROM downloads ORDER BY finished DESC, id DESC "
            "LIMIT ?", (limit,))]

    def download_remove(self, ident):
        self._run("DELETE FROM downloads WHERE id = ?", (ident,))


def open_library():
    """the library, or None after saying why"""
    from vts import log
    try:
        return Library()
    except LibError as e:
        log.err(str(e))
        return None
    except OSError as e:
        log.err(f"no place for the library: {e.strerror}")
        return None

# SPDX-License-Identifier: Apache-2.0
"""
The frame of the full screen app: the screens, lists, input, keys, the
player and the downloads. The screens themselves are in screens.py.
"""

import base64
import curses
import os
import sys
import threading
import time

from vts import config, log, platform, player, update, util
from vts import text as T
from vts.library import EXISTS, WATCH_LATER, LibError, Library
from vts.rules import BadRules, valid_channel_id
from vts.tui import draw as D
from vts.youtube import Client, YouTubeError

TICK = 0.25
MESSAGE_TIME = 4.0
STARTUP_MESSAGE_TIME = 20.0
DONE_MESSAGE_TIME = 10.0
SUGGEST_IDLE = 0.3
THUMB_TICK = 0.05  # while thumbnails are on their way
PANE_MIN_COLS = 100  # the preview needs a terminal this wide
MAX_DEPTH = 32
SEEK = 10  # seconds, , and .
QUALITY_SETTING = None  # as the settings say

_KEYS = {
    curses.KEY_UP: "UP", curses.KEY_DOWN: "DOWN", curses.KEY_LEFT: "LEFT",
    curses.KEY_RIGHT: "RIGHT", curses.KEY_NPAGE: "PGDN",
    curses.KEY_PPAGE: "PGUP", curses.KEY_HOME: "HOME", curses.KEY_END: "END",
    curses.KEY_ENTER: "ENTER", curses.KEY_BACKSPACE: "BACKSPACE",
    curses.KEY_DC: "DELETE", curses.KEY_BTAB: "BTAB",
    curses.KEY_MOUSE: "MOUSE", curses.KEY_RESIZE: "RESIZE",
}
_CHARS = {"\n": "ENTER", "\r": "ENTER", "\x7f": "BACKSPACE",
          "\x08": "BACKSPACE", "\x1b": "ESC"}
ENTER_KEYS = ("ENTER", "RIGHT", "l")


def read_key(timeout):
    """the next key, or None after @timeout seconds"""
    D.scr.timeout(int(timeout * 1000) if timeout >= 0 else -1)
    try:
        k = D.scr.get_wch()
    except curses.error:
        return None
    except KeyboardInterrupt:
        return "INTERRUPT"
    if isinstance(k, int):
        return _KEYS.get(k, f"KEY{k}")
    return _CHARS.get(k, k)


class DownloadJob:
    def __init__(self, dl, item, folder, label):
        self.dl = dl
        self.item = item
        self.title = item.get("title")
        self.folder = folder
        self.label = label

    def state(self):
        """ "45% 2.1 MB/s 0:32 left" for the top bar"""
        p = self.dl.progress
        if p.finishing:
            return "finishing"
        if not p.files:
            return "starting"
        s = (f"{int(p.done * 100 / p.total)}%" if p.total > 0
             else f"{p.done / 1e6:.1f} MB")
        if p.files > 1:
            s += f" (part {p.files})"
        if p.speed > 0:
            s += f" {p.speed / 1e6:.1f} MB/s"
        if p.eta >= 0:
            s += f" {util.format_time(p.eta)} left"
        return s


class Shown:
    """the thumbnail on the screen, if any"""

    def __init__(self, ident=None, y=0, x=0, thumb=None):
        self.ident = ident
        self.y = y
        self.x = x
        self.thumb = thumb

    def same(self, o):
        return (self.ident == o.ident and (not self.ident or (
            self.y == o.y and self.x == o.x and self.thumb is o.thumb)))


HELP = (
    "Moving around",
    "  Up/Down j/k, PgUp/PgDn, Home/End g/G   move in the list",
    "  Enter                                   open",
    "  Backspace h Esc                         back",
    "  1 Home  2 Favourites  3 Library  4 Settings",
    "  /  s                                    search (recent ones first)",
    "  r                                       reload",
    "On a video",
    "  p  play        P  play from here on    a  sound only",
    "  Q  play in a quality you choose        d  download (video or sound)",
    "  u  play with the subtitles you choose  A  add to what plays",
    "  w  Watch later +  add to a playlist    y  copy the link",
    "  c  its channel f  favourite channel",
    "While playing",
    "  Space  pause   < >  previous, next     x  stop",
    "  , .  back, forward 10 s   9 0  volume   m  mute",
    "  T  go to a chapter   V  another quality   u  other subtitles",
    "On a video page: C comments  t chapters  D whole description",
    "On a channel: Tab next tab, / search in it, f favourite",
    "In your lists, History, Favourites, Downloads: - or Delete removes",
    "In Favourites: N add a channel, i import, e export",
    "In Settings: Enter change, d the default, Update now, Check now",
    "q quit   ? this help",
)


class App:
    def __init__(self, cfg, lib, yt):
        self.cfg = cfg
        self.lib = lib
        self.yt = yt
        self.stack = []
        self.player = None
        self.playing = None  # title of what plays
        self.quality = cfg.quality  # and in which quality
        self.sound = cfg.sound_quality  # of the sound, for the sound only
        self.subs = ""  # with which subtitles ("" as the settings say)
        self.no_subs = False  # or none
        self.downloads = []  # DownloadJob
        self.message = ""
        self.message_error = False
        self.message_until = 0.0
        self.cols = 80  # for the screen's own drawing: less with the pane
        self.quit = False
        self.help = False
        self.overlay = False  # a prompt or a menu over the screen
        self.thumbs = None
        self.shown = Shown()
        self.entry_y = []  # where each entry was drawn, for the mouse
        self.entry_first = 0
        self.marks = {}  # video id -> (position, length, watched)
        self.chapters = {}  # video id -> chapters, for T
        self._updating = None

    # --- screens -----------------------------------------------------------

    def push(self, s):
        # a screen that couldn't load has already said why
        if s is None:
            return
        if len(self.stack) == MAX_DEPTH:
            # forget the oldest, keep the root
            del self.stack[1]
        self.stack.append(s)

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()

    def root(self, s):
        if s is not None:
            self.stack = [s]

    def top(self):
        return self.stack[-1] if self.stack else None

    # --- messages ----------------------------------------------------------

    def say(self, msg, error=False, seconds=MESSAGE_TIME):
        self.message = T.clean(str(msg))
        self.message_error = error
        self.message_until = time.monotonic() + seconds

    def failed(self, e):
        self.say(str(e), True)

    def draw_bottom(self, s, error=False):
        y = D.lines() - 1
        D.clear_line(y)
        D.text(y, 0, s, D.cols(), D.pair(D.C_ERROR) | curses.A_BOLD
               if error else D.pair(D.C_DIM))

    def busy(self, what):
        """say "Loading..." now, before something that takes a while"""
        self.draw_bottom(what)
        D.scr.refresh()

    # --- drawing -----------------------------------------------------------

    def draw_top(self):
        D.hline(0, 0, D.cols(), D.pair(D.C_BAR))
        left = " vts" + "".join(f" › {s.title}" for s in self.stack)
        right = ""
        if self.downloads:
            # the first one, and how many more
            right = "⬇ " + self.downloads[0].state()
            if len(self.downloads) > 1:
                right += f" +{len(self.downloads) - 1}"
            right += "  " if self.player else " "
        if self.player:
            _, pos, length = self.player.now()
            mark = "❚❚" if self.player.paused else "▶"
            right += (f"{mark} {util.format_time(max(0, pos))}/"
                      f"{util.format_time(length) if length else '--'} "
                      f"{self.playing or ''} ")
        w = min(T.width(right), D.cols() // 2)
        D.text(0, 0, left, D.cols() - w - 1, D.pair(D.C_BAR) | curses.A_BOLD)
        if w:
            D.text(0, D.cols() - w, right, w, D.pair(D.C_BAR))

    def mark(self, it):
        """ "watched" or "stopped at 3:20" for a video in the history"""
        m = self.marks.get(it["id"])
        if not m or (it["published"] or "").startswith(("stopped at",
                                                        "watched")):
            return None
        pos, length, watched = m
        if watched:
            return "✓ watched"
        if pos > 0:
            return f"stopped at {util.format_time(pos)}"
        return None

    def details(self, it):
        """the second line of a result: what it is, how long, who, how seen"""
        def parts(*ss):
            return " · ".join(s for s in ss if s)
        if it.kind == "channel":
            return parts("channel", it["handle"], it["subscribers"])
        if it.kind == "playlist":
            return parts("playlist", it["videos"], it["channel"])
        if it.kind in ("setting", "doctor", "download", "action"):
            return it.get("details")
        when = it["published"]
        if not when and it["date"]:
            when = util.ago(it["date"]) or it["date"]
        length = ("LIVE" if "live" in it.f else "upcoming"
                  if "upcoming" in it.f else it["duration"])
        return parts("Short" if it.kind == "short" else None, length,
                     it["channel"], it["views"], when, self.mark(it))

    def entry_draw(self, s, i, y, rows, selected):
        it = s.items[i]
        attr = curses.A_REVERSE | curses.A_BOLD if selected else curses.A_BOLD
        D.put(y, 0, "▌" if selected else " ")
        D.text(y, 1, it.get("title"), self.cols - 2, attr)
        if rows > 1:
            D.text(y + 1, 3, self.details(it), self.cols - 4,
                   D.pair(D.C_DIM))

    def entry_rows(self, s, i):
        rows = s.entry_rows(self, i)
        return rows + (1 if i in s.labels else 0)

    def scroll_to(self, s, rows):
        """make the selection visible in @rows lines"""
        s.top = min(s.top, s.sel)
        while True:
            used = sum(self.entry_rows(s, i)
                       for i in range(s.top, min(s.sel + 1, len(s.items))))
            if used <= rows or s.top >= s.sel:
                break
            s.top += 1

    def draw_list(self, s, y0, rows):
        self.entry_first = s.top
        self.entry_y = []
        if not s.items:
            D.text(y0 + 1, 2, "Nothing here." if s.done else "",
                   self.cols - 4, D.pair(D.C_DIM))
            return
        self.scroll_to(s, rows)
        y = y0
        for i in range(s.top, len(s.items)):
            label = s.labels.get(i)
            r = self.entry_rows(s, i) - (1 if label is not None else 0)
            if y + r + (1 if label is not None else 0) > y0 + rows:
                break
            if label is not None:
                D.text(y, 1, label, self.cols - 2,
                       D.pair(D.C_ACCENT) | curses.A_BOLD)
                y += 1
            self.entry_y.append(y)
            if s.draws_entries:
                s.entry_draw(self, i, y, r, i == s.sel)
            else:
                self.entry_draw(s, i, y, r, i == s.sel)
            y += r

    def draw_help(self):
        D.scr.erase()
        D.text(0, 0, " vts – keys", D.cols(),
               D.pair(D.C_BAR) | curses.A_BOLD)
        for i, line in enumerate(HELP):
            if i + 2 >= D.lines() - 1:
                break
            D.text(i + 2, 1, line, D.cols() - 2,
                   0 if line.startswith(" ") else curses.A_BOLD)
        self.draw_bottom("Any key to go back")

    # --- the preview on the right, with the thumbnail ---------------------

    def pane_width(self):
        if D.cols() < PANE_MIN_COLS:
            return 0
        return 40 if D.cols() < 160 else 52

    def has_pictures(self, s):
        from vts import thumbs
        if s.draws_entries:
            return False  # comments
        return any(thumbs.thumb_id(it) for it in s.items[:50])

    def term_write(self, data):
        platform.term_write(data)

    def hide_thumb(self):
        """
        take the thumbnail off the screen: curses doesn't know it is there,
        so it repaints everything, and kitty's images go away on their own
        command
        """
        if not self.shown.ident:
            return
        if self.thumbs and self.thumbs.mode == "kitty":
            from vts.thumbs import KITTY_DELETE
            self.term_write(KITTY_DELETE)
        D.scr.redrawwin()
        self.shown = Shown()

    def put_thumb(self, th, y, x):
        """write @th at @y, @x, where curses drew nothing"""
        # ESC 7 and ESC 8: where curses left the cursor, and back
        out = ["\x1b7"]
        rows = th.text.split("\n") if th.lines else [th.text]
        for row, part in enumerate(rows):
            out.append(f"\x1b[{y + row + 1};{x + 1}H{part}")
        out.append("\x1b[0m\x1b8")
        self.term_write("".join(out))

    def draw_pane(self, s, w):
        from vts import thumbs
        want = Shown()
        it = s.items[s.sel] if s.items else None
        x = D.cols() - w + 1
        cols = w - 2
        for y in range(1, D.lines() - 1):
            D.text(y, D.cols() - w - 1, "│", 1, D.pair(D.C_DIM))
        y = 2
        if not it:
            return want
        cw, ch = thumbs.cell_size()
        # the room of a 16:9 picture
        rows = min((cols * 9 * cw) // (16 * ch), D.lines() // 2)
        ident = thumbs.thumb_id(it)
        got = self.thumbs.get(ident, cols, rows) if ident else False
        if got:
            want = Shown(ident, y, x + (cols - got.cols) // 2, got)
            y += got.rows + 1
        elif got is None:
            D.text(y + rows // 2, x + cols // 2 - 1, "…", 1,
                   D.pair(D.C_DIM))
            y += rows + 1
        y += D.wrapped(y, x, it.get("title"), cols, 4, curses.A_BOLD)
        y += D.wrapped(y, x, self.details(it), cols, 3, D.pair(D.C_DIM))
        if it["description"] and y + 1 < D.lines() - 1:
            D.wrapped(y + 1, x, it["description"], cols,
                      D.lines() - 2 - (y + 1))
        return want

    def _load_marks(self, s):
        ids = [it["id"] for it in s.items[s.top:s.top + 60]
               if it.kind in ("video", "short") and it["id"] not in
               self.marks]
        if ids:
            try:
                got = self.lib.hist_marks(ids)
            except LibError:
                got = {}
            for i in ids:
                self.marks[i] = got.get(i)

    def draw(self):
        s = self.top()
        if self.help:
            self.hide_thumb()
            self.draw_help()
            D.scr.refresh()
            return
        w = 0
        if self.thumbs and s and self.has_pictures(s):
            w = self.pane_width()
        self.cols = D.cols() - w - 1 if w else D.cols()
        if s:
            self._load_marks(s)
        D.scr.erase()
        self.draw_top()
        y = 1
        if s:
            y = s.header(self, 1)
        rows = D.lines() - 1 - y
        if s and rows > 0:
            self.draw_list(s, y, rows)
        want = Shown()
        if w:
            want = self.draw_pane(s, w)
        if time.monotonic() < self.message_until:
            self.draw_bottom(self.message, self.message_error)
        else:
            self.draw_bottom(s.hints if s and s.hints else "? help  q quit")
        if self.overlay:
            want = Shown()  # it would cover the menu
        if not want.same(self.shown):
            self.hide_thumb()
        D.scr.refresh()
        if want.ident and not self.shown.ident:
            self.put_thumb(want.thumb, want.y, want.x)
            self.shown = want

    # --- input -------------------------------------------------------------

    def prompt(self, label, initial="", suggest=False, recent=False):
        """
        a line of text asked at the bottom of the screen; with @suggest,
        YouTube's search suggestions; with @recent, the recent searches.
        The text, or None when cancelled.
        """
        buf = list(initial or "")
        cur = len(buf)
        typed = 0.0
        sugg = []  # (text, is_recent)
        pick = -1
        asked = [None, None]  # [the text asked for, its answer]
        lock = threading.Lock()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        overlay = self.overlay
        self.overlay = True

        def recents(prefix):
            if not recent:
                return []
            try:
                return [(q, True) for q in self.lib.searches(
                    prefix, 8 if not prefix else 3) if q != prefix]
            except LibError:
                return []

        def fetch(q):
            try:
                got = self.yt.suggest(q)
            except YouTubeError:
                got = []
            with lock:
                if asked[0] == q:
                    asked[1] = got

        sugg = recents("")
        result = None
        try:
            while True:
                text = "".join(buf)
                with lock:
                    if asked[1] is not None and asked[0] == text:
                        seen = {q for q, _ in sugg if _}
                        sugg = [x for x in sugg if x[1]] + [
                            (q, False) for q in asked[1] if q not in seen]
                        sugg = sugg[:10]
                        asked[1] = None
                lw = T.width(label) + 2
                n = len(sugg)
                self.draw()
                # suggestions above the input line
                for i in range(min(n, D.lines() - 3)):
                    y = D.lines() - 2 - n + i
                    D.clear_line(y)
                    q, is_recent = sugg[i]
                    attr = curses.A_REVERSE if i == pick else 0
                    if is_recent:
                        D.text(y, 2, "↺ " + q, D.cols() - 4,
                               attr | D.pair(D.C_DIM))
                    else:
                        D.text(y, 2, q, D.cols() - 4, attr)
                y = D.lines() - 1
                D.clear_line(y)
                D.text(y, 0, label, D.cols(), curses.A_BOLD)
                D.put(y, T.width(T.fit(label, D.cols())), ": ")
                D.text(y, lw, text, D.cols() - lw - 1)
                # how to get out, while there is room for it
                if not text and D.cols() - lw > 40:
                    D.text(y, lw, "Enter to go, Esc or Backspace to cancel",
                           D.cols() - lw - 1, D.pair(D.C_DIM))
                try:
                    D.scr.move(y, min(D.cols() - 1,
                                      lw + T.width("".join(buf[:cur]))))
                except curses.error:
                    pass
                D.scr.refresh()
                key = read_key(TICK)
                if key is None:
                    if suggest and typed and \
                            time.monotonic() - typed >= SUGGEST_IDLE:
                        typed = 0.0
                        pick = -1
                        sugg = recents(text)
                        if text:
                            with lock:
                                asked[0] = text
                                asked[1] = None
                            threading.Thread(target=fetch, args=(text,),
                                             daemon=True).start()
                    continue
                if key == "ENTER":
                    if pick >= 0:
                        buf = list(sugg[pick][0])
                    result = "".join(buf) or None
                    break
                if key in ("ESC", "\x07", "INTERRUPT"):
                    break
                if key == "BACKSPACE":
                    # nothing left to erase: back where it was
                    if not buf:
                        break
                    if cur:
                        del buf[cur - 1]
                        cur -= 1
                    typed = time.monotonic()
                    pick = -1
                elif key == "DELETE":
                    if 0 <= pick < n and sugg[pick][1]:
                        # a recent search forgotten
                        try:
                            self.lib.search_remove(sugg[pick][0])
                        except LibError:
                            pass
                        del sugg[pick]
                        pick = min(pick, len(sugg) - 1)
                    elif cur < len(buf):
                        del buf[cur]
                        typed = time.monotonic()
                elif key == "LEFT":
                    cur = max(0, cur - 1)
                elif key == "RIGHT":
                    cur = min(len(buf), cur + 1)
                elif key in ("HOME", "\x01"):
                    cur = 0
                elif key in ("END", "\x05"):
                    cur = len(buf)
                elif key == "\x15":
                    buf = []
                    cur = 0
                    typed = time.monotonic()
                elif key == "UP":
                    if n:
                        pick = n - 1 if pick <= 0 else pick - 1
                elif key in ("DOWN", "\t"):
                    if n:
                        pick = 0 if pick >= n - 1 else pick + 1
                elif len(key) == 1 and ord(key) >= 0x20 and key != "\x7f":
                    buf.insert(cur, key)
                    cur += 1
                    typed = time.monotonic()
                    pick = -1
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.overlay = overlay
        return result

    def choose(self, title, choices, current=0):
        """pick one of @choices: its index, or -1 when cancelled"""
        overlay = self.overlay
        self.overlay = True
        try:
            return self._choose(title, choices, current)
        finally:
            self.overlay = overlay

    def _choose(self, title, choices, current):
        n = len(choices)
        if not n:
            return -1
        sel = current if 0 <= current < n else 0
        top = 0
        w = max([T.width(title) + 4] + [T.width(c) + 6 for c in choices])
        while True:
            w2 = min(w, D.cols() - 2)
            h = min(n + 2, D.lines() - 2)
            x = (D.cols() - w2) // 2
            y = (D.lines() - h) // 2
            self.draw()
            for i in range(h):
                D.hline(y + i, x, w2, D.pair(D.C_BAR))
            D.text(y, x + 1, title, w2 - 2, D.pair(D.C_BAR) | curses.A_BOLD)
            # more choices than room: the selected one stays in view
            if sel < top:
                top = sel
            elif h > 2 and sel >= top + h - 2:
                top = sel - (h - 3)
            for i in range(top, min(n, top + h - 2)):
                D.text(y + 1 + i - top, x + 2, choices[i], w2 - 4,
                       D.pair(D.C_BAR) | (curses.A_REVERSE if i == sel
                                          else 0))
            D.scr.refresh()
            key = read_key(-1)
            if key in ("UP", "k"):
                sel = sel - 1 if sel else n - 1
            elif key in ("DOWN", "j", "\t"):
                sel = 0 if sel == n - 1 else sel + 1
            elif key in ("HOME", "g"):
                sel = 0
            elif key in ("END", "G"):
                sel = n - 1
            elif key == "PGDN":
                sel = min(n - 1, sel + max(1, h - 3))
            elif key == "PGUP":
                sel = max(0, sel - max(1, h - 3))
            elif key == "ENTER":
                return sel
            elif key in ("ESC", "q", "BACKSPACE", "INTERRUPT"):
                return -1
            elif key and len(key) == 1 and "1" <= key <= "9" and \
                    int(key) - 1 < n:
                return int(key) - 1

    def confirm(self, question):
        return self.choose(question, ["No", "Yes"], 0) == 1

    # --- actions -----------------------------------------------------------

    def open(self, it):
        from vts.tui import screens as S
        if not it:
            return
        if it.kind == "channel":
            self.push(S.channel(self, it["id"]))
        elif it.kind == "playlist":
            self.push(S.playlist(self, it["id"]))
        elif it.kind in ("video", "short"):
            self.push(S.video(self, it["id"]))

    def open_link(self, q):
        """
        a link or a @handle typed in the search box opens what it names
        (YouTube's search often finds nothing for them): whether it was one
        """
        from vts import channel as ch
        from vts import video as video_mod
        from vts.tui import screens as S
        q = q.strip()
        if not q or any(c.isspace() for c in q):
            return False
        link = "/" in q or "youtu" in q
        if link and video_mod.ref(q):
            self.push(S.video(self, video_mod.ref(q)))
            return True
        if link and ch.playlist_ref(q):
            self.push(S.playlist(self, ch.playlist_ref(q)))
            return True
        if not (q.startswith("@") or (link and any(
                k in q for k in ("/@", "/channel/", "/c/", "/user/")))):
            return False
        self.busy(f"Looking for {q}...")
        try:
            ident = ch.resolve(self.yt, q)
        except YouTubeError as e:
            self.failed(e)
            return True
        self.push(S.channel(self, ident))
        return True

    def stop_player(self):
        """whether mpv ended on its own without playing (said)"""
        if not self.player:
            return False
        _, problem = self.player.finish()
        self.player = None
        self.playing = None
        self.marks.clear()
        if problem:
            log.warn(problem)
            self.say(problem, True)
            return True
        return False

    def choose_sound(self, current):
        """high, medium or low, from a menu; None when given up"""
        qs = config.SOUND_QUALITIES
        i = self.choose("Sound quality?", [player.sound_name(q) for q in qs],
                        qs.index(current) if current in qs else 0)
        return None if i < 0 else qs[i]

    def choose_quality(self, title, current, sound):
        """(quality, sound quality) from a menu, or None when given up"""
        names = [player.quality_name(q) for q in player.QUALITIES]
        cur = player.QUALITIES.index(current) if current in \
            player.QUALITIES else 0
        i = self.choose(title, names, cur)
        if i < 0:
            return None
        q = player.QUALITIES[i]
        if q == config.QUALITY_AUDIO:
            sound = self.choose_sound(sound)
            if sound is None:
                return None
        return q, sound

    def play_choose(self, it):
        if not it or it.kind not in ("video", "short"):
            self.say("only videos can be played", True)
            return
        got = self.choose_quality("Play in which quality?", self.cfg.quality,
                                  self.cfg.sound_quality)
        if got:
            self.play([it], *got)

    def play_opts(self, quality, sound=None):
        """the options of what plays now: its quality and subtitles"""
        o = player.PlayOptions(self.cfg)
        o.quality = quality
        o.sound = sound or self.sound
        if self.no_subs:
            o.no_subs = True
            o.subs = None
        elif self.subs:
            o.subs = self.subs
        return o

    def restart(self, o):
        """what plays, again with @o, from where it is"""
        try:
            self.player = self.player.restart(o)
            return True
        except player.PlayError as e:
            self.player = None
            self.playing = None
            self.say(str(e), True)
            return False

    def subtitles(self, it):
        now = self.player.current() if self.player else None
        if it and it.kind not in ("video", "short"):
            it = None
        # while a video plays, its subtitles; otherwise those of @it
        playing = now is not None
        if playing:
            it = now
        if not it:
            self.say("Choose a video first", True)
            return
        self.busy("Looking for subtitles...")
        try:
            subs = player.subtitles(self.cfg.downloader, it["id"],
                                    self.cfg.language)
        except player.PlayError as e:
            self.say(str(e), True)
            return
        if not subs:
            self.say("This video has no subtitles")
            return
        names = ["No subtitles"] + [s.name for s in subs]
        current = 0
        if playing and not self.no_subs:
            for i, s in enumerate(subs):
                if s.code == self.subs:
                    current = i + 1
        pick = self.choose("Subtitles", names, current)
        if pick < 0:
            return
        sub = subs[pick - 1] if pick else None
        self.no_subs = sub is None
        self.subs = sub.code if sub else ""
        if playing and self.player:
            if self.restart(self.play_opts(self.quality)):
                self.say(f"Subtitles: {names[pick]}")
        else:
            self.play_with([it], QUALITY_SETTING)

    def change_quality(self):
        """what plays, again in another quality, from where it is"""
        if not self.player:
            self.say("Nothing is playing")
            return
        got = self.choose_quality("Go on in which quality?", self.quality,
                                  self.sound)
        if got is None or not self.player:
            return
        q, sound = got
        if self.restart(self.play_opts(q, sound)):
            self.quality, self.sound = q, sound
            self.say(f"Playing in {player.play_name(q, sound)}")

    def play(self, items, quality=QUALITY_SETTING, sound=None):
        # a new video: the subtitles of the settings
        self.subs = ""
        self.no_subs = False
        self.play_with(items, quality, sound=sound)

    def play_with(self, items, quality, start=-1, sound=None):
        # only videos and Shorts: a queue skips the rest
        videos = [it for it in items if it.kind in ("video", "short")]
        if not videos:
            self.say("nothing to play here", True)
            return
        self.stop_player()
        o = self.play_opts(quality if quality is not None
                           else self.cfg.quality,
                           sound or self.cfg.sound_quality)
        o.start = start
        self.quality, self.sound = o.quality, o.sound
        try:
            self.player = player.Player(self.cfg.player, self.lib, videos, o)
        except player.PlayError as e:
            self.say(str(e), True)
            return
        self.playing = videos[0]["title"]
        self.say(f"Playing{' a queue' if len(videos) > 1 else ''}, "
                 f"{player.play_name(o.quality, o.sound)}")

    def play_file(self, path, title):
        """a file downloaded before, with mpv"""
        if not os.path.isfile(path):
            self.say(f"The file is gone: {path}", True)
            return
        self.stop_player()
        from vts.rules import Item
        o = player.PlayOptions(self.cfg)
        o.ytdl = None
        o.resume = False
        try:
            self.player = player.Player(self.cfg.player, None, [
                Item("video", id="file", title=title, file=path)], o)
        except player.PlayError as e:
            self.say(str(e), True)
            return
        self.playing = title
        self.say("Playing the file")

    def enqueue(self, it):
        """add @it after what plays"""
        if not it or it.kind not in ("video", "short"):
            return
        if not self.player:
            self.play([it])
            return
        if self.player.append(it):
            self.say(f"Added to what plays: {it.get('title')}")
        else:
            self.say("mpv didn't take it", True)

    def poll_player(self):
        if self.player and not self.player.poll(0) and not self.stop_player():
            self.say("Finished playing")

    def player_key(self, key):
        """the keys of what plays; whether @key was one"""
        p = self.player
        if key == "x" and p:
            self.stop_player()
            self.say("Stopped")
        elif key == " " and p:
            p.command("cycle", "pause")
        elif key == ">" and p:
            p.command("playlist-next")
        elif key == "<" and p:
            p.command("playlist-prev")
        elif key in (",", ".") and p:
            p.command("seek", str(-SEEK if key == "," else SEEK),
                      "relative")
        elif key in ("9", "0") and p:
            p.command("add", "volume", "-5" if key == "9" else "5")
            if p.volume is not None:
                self.say(f"Volume {int(p.volume) + (-5 if key == '9' else 5)}")
        elif key == "m" and p:
            p.command("cycle", "mute")
        elif key == "T":
            self.chapter_of_playing()
        elif key == "V":
            self.change_quality()
        elif key in (" ", ">", "<", ",", ".", "9", "0", "m", "x"):
            self.say("Nothing is playing")
        else:
            return False
        return True

    def chapter_of_playing(self):
        from vts import video
        now = self.player.current() if self.player else None
        if not now or now["id"] == "file":
            self.say("Nothing is playing")
            return
        ch = self.chapters.get(now["id"])
        if ch is None:
            self.busy("Loading the chapters...")
            try:
                ch = video.get(self.yt, now["id"]).chapters
            except YouTubeError as e:
                self.failed(e)
                return
            self.chapters[now["id"]] = ch
        if not ch:
            self.say("No chapters")
            return
        pick = self.choose("Go to", [f"{util.format_time(c.start):>8}  "
                                     f"{c.title}" for c in ch])
        if pick >= 0 and self.player:
            self.player.command("seek", str(ch[pick].start), "absolute")

    # --- downloads ---------------------------------------------------------

    SOUND = (None, "mp3", "m4a", "opus", "flac")
    SOUND_NAMES = ("Sound only, as YouTube has it", "Sound only, MP3",
                   "Sound only, M4A", "Sound only, Opus", "Sound only, FLAC")

    def download(self, it):
        """the video in a quality, or the sound, chosen from a menu"""
        if not it or it.kind not in ("video", "short"):
            self.say("only videos can be downloaded", True)
            return
        qs = player.QUALITIES[:-1]
        names = [f"Video, {player.quality_name(q)}" for q in qs] + \
            list(self.SOUND_NAMES)
        cur = (len(qs) if self.cfg.quality == config.QUALITY_AUDIO
               else qs.index(self.cfg.quality) if self.cfg.quality in qs
               else 0)
        pick = self.choose("Download", names, cur)
        if pick < 0:
            return
        sound = "high"
        name = names[pick]
        if pick < len(qs):
            quality, fmt = qs[pick], None
        else:
            quality, fmt = config.QUALITY_AUDIO, self.SOUND[pick - len(qs)]
            sound = self.choose_sound(self.cfg.sound_quality)
            if sound is None:
                return
            name += f", {sound} quality"
        try:
            folder = self.cfg.downloads_dir()
        except OSError:
            folder = None
        if not folder:
            self.say("the folder for downloads can't be made: see downloads "
                     "in the settings", True)
            return
        try:
            dl = player.Download(self.cfg.downloader, folder, it, quality,
                                 fmt, sound)
        except FileNotFoundError:
            self.say(f"{self.cfg.downloader} is not installed", True)
            return
        except OSError:
            self.say(f"{self.cfg.downloader} didn't start", True)
            return
        self.downloads.append(DownloadJob(dl, it, folder, name))
        self.say(f"Downloading ({name}): the top bar follows it")

    def poll_downloads(self):
        for job in list(self.downloads):
            if job.dl.running():
                continue
            # what it said last
            job.dl.poll()
            self.downloads.remove(job)
            p = job.dl.progress
            if job.dl.cancelled:
                self.say(f"Download stopped: {job.title}")
            elif job.dl.status and p.error:
                # the reason first: a title can fill the line
                self.say(f"Download failed, {p.error}: {job.title}", True,
                         DONE_MESSAGE_TIME)
            elif job.dl.status:
                self.say(f"Download failed: {job.title}", True,
                         DONE_MESSAGE_TIME)
            else:
                self.say(f"✓ Downloaded: {job.title}, in {job.folder}",
                         False, DONE_MESSAGE_TIME)
                try:
                    self.lib.download_add(job.item, job.dl.file() or
                                          job.folder, job.dl.quality ==
                                          config.QUALITY_AUDIO)
                except LibError as e:
                    log.warn(f"downloads: {e}")

    # --- more actions ------------------------------------------------------

    def copy_link(self, it):
        """the link, to the terminal's clipboard (OSC 52)"""
        ident = it["id"] if it else None
        if not ident:
            return
        if it.kind == "channel":
            url = f"https://www.youtube.com/channel/{ident}"
        elif it.kind == "playlist":
            url = f"https://www.youtube.com/playlist?list={ident}"
        else:
            url = f"https://youtu.be/{ident}"
        self.term_write(f"\x1b]52;c;{base64.b64encode(url.encode()).decode()}"
                        "\a")
        self.say(f"Copied {url}")

    def watch_later(self, it):
        if not it or it.kind not in ("video", "short"):
            return
        try:
            self.lib.list_add(WATCH_LATER, it)
            self.say("Added to Watch later")
        except LibError as e:
            self.say(str(e), e.kind != EXISTS)

    def add_to_list(self, it):
        if not it or it.kind not in ("video", "short"):
            return
        try:
            lists = [pl.name for pl in self.lib.lists()]
        except LibError as e:
            self.failed(e)
            return
        pick = self.choose("Add to", lists + ["New playlist..."])
        name = None
        if pick == len(lists):
            name = self.prompt("Name of the new playlist")
            if name:
                try:
                    self.lib.list_create(name)
                except LibError as e:
                    self.failed(e)
                    if e.kind != EXISTS:
                        return
        elif pick >= 0:
            name = lists[pick]
        if not name:
            return
        try:
            self.lib.list_add(name, it)
            self.say(f"Added to {name}")
        except LibError as e:
            if e.kind == EXISTS:
                self.say(f"Already in {name}")
            else:
                self.failed(e)

    def favourite(self, channel_id, name, handle=None):
        if not valid_channel_id(channel_id):
            self.say("no channel to keep", True)
            return
        fav = self.lib.fav(channel_id)
        if fav:
            if self.confirm(f"Remove {fav.name} from favourites?"):
                try:
                    self.lib.fav_remove(channel_id)
                    self.say(f"Removed {fav.name}")
                except LibError as e:
                    self.failed(e)
            return
        try:
            self.lib.fav_add(channel_id, name or channel_id, handle)
            self.say(f"{name or channel_id} is a favourite: its new videos "
                     "will be on Home")
        except LibError as e:
            self.failed(e)

    # --- the keys of every list --------------------------------------------

    def list_key(self, s, key):
        n = len(s.items)
        it = s.items[s.sel] if n else None
        page = max(1, D.lines() // 4)
        if key in ("DOWN", "j"):
            if s.sel + 1 < n:
                s.sel += 1
        elif key in ("UP", "k"):
            if s.sel:
                s.sel -= 1
        elif key == "PGDN":
            s.sel = min(s.sel + page, max(0, n - 1))
        elif key == "PGUP":
            s.sel = max(0, s.sel - page)
        elif key in ("HOME", "g"):
            s.sel = 0
        elif key in ("END", "G"):
            s.sel = max(0, n - 1)
        elif key in ENTER_KEYS:
            self.open(it)
        elif key == "p":
            if it:
                self.play([it])
        elif key == "a":
            if it:
                self.play([it], config.QUALITY_AUDIO)
        elif key == "Q":
            self.play_choose(it)
        elif key == "u":
            self.subtitles(it)
        elif key == "P":
            if it:
                self.play(s.items[s.sel:])
        elif key == "A":
            self.enqueue(it)
        elif key == "w":
            self.watch_later(it)
        elif key == "+":
            self.add_to_list(it)
        elif key == "d":
            self.download(it)
        elif key == "y":
            self.copy_link(it)
        elif key == "c":
            if it and it.kind != "channel" and valid_channel_id(
                    it["channel_id"]):
                from vts.tui import screens as S
                self.push(S.channel(self, it["channel_id"]))
        elif key == "f":
            if it and it.kind == "channel":
                self.favourite(it["id"], it["title"], it["handle"])
            elif it:
                self.favourite(it["channel_id"], it["channel"])
        else:
            return False
        return True

    def on_mouse(self, s):
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        up = getattr(curses, "BUTTON4_PRESSED", 0)
        down = getattr(curses, "BUTTON5_PRESSED", 0)
        if up and bstate & up:
            self.list_key(s, "UP")
            return
        if down and bstate & down:
            self.list_key(s, "DOWN")
            return
        if not bstate & (curses.BUTTON1_CLICKED |
                         curses.BUTTON1_DOUBLE_CLICKED) or mx >= self.cols:
            return
        for i in range(len(self.entry_y), 0, -1):
            if my >= self.entry_y[i - 1]:
                s.sel = self.entry_first + i - 1
                if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                    self.on_key("ENTER")
                return

    def on_key(self, key):
        from vts.tui import screens as S
        s = self.top()
        if self.help:
            self.help = False
            return
        if key == "MOUSE":
            self.on_mouse(s)
            return
        if key == "RESIZE":
            curses.update_lines_cols()
            # curses paints the whole screen again
            self.hide_thumb()
            return
        if s.key(self, key) or self.list_key(s, key):
            self.more(self.top())
            return
        if key == "q":
            if len(self.stack) > 1:
                self.pop()
            else:
                self.quit = True
        elif key in ("BACKSPACE", "LEFT", "h", "ESC"):
            self.pop()
        elif key == "?":
            self.help = True
        elif key == "1":
            self.root(S.home(self))
        elif key == "2":
            self.root(S.favourites(self))
        elif key == "3":
            self.root(S.library(self))
        elif key == "4":
            self.root(S.settings(self))
        elif key in ("/", "s"):
            q = self.prompt("Search", suggest=True, recent=True)
            if q:
                try:
                    self.lib.search_add(q)
                except LibError:
                    pass
                if not self.open_link(q):
                    self.push(S.search(self, q))
        elif key == "r":
            s.sel = s.top = 0
            s.reload(self)
        elif key == "u":
            # screens without a list of videos: what plays
            self.subtitles(None)
        else:
            self.player_key(key)

    def more(self, s):
        """the end of the list: its next page"""
        if s and not s.done and s.items and s.sel + 3 >= len(s.items):
            self.busy("Loading more...")
            if not s.more(self):
                s.done = True

    # --- updates in the background -----------------------------------------

    def start_updates(self):
        """the rules once a day, yt-dlp once a week, without waiting"""
        if self.cfg.offline or not self.cfg.updates:
            return
        if update.due("rules", update.RULES_EVERY):
            update.done("rules")
            rev = self.yt.rules.revision

            def rules():
                try:
                    new = update.update_rules(rev)
                    if new:
                        log.info(f"rules updated to revision {new.revision}")
                        self.yt.set_rules(new)
                except (update.UpdateError, BadRules) as e:
                    log.dbg(f"rules update: {e}")
            self._updating = threading.Thread(target=rules, daemon=True)
            self._updating.start()
        if update.due("yt-dlp", update.YTDLP_EVERY):
            update.done("yt-dlp")
            try:
                update.ytdlp_update(self.cfg.downloader, background=True)
            except PermissionError:
                log.dbg("yt-dlp not updated: the system's")
            except OSError as e:
                log.dbg(f"yt-dlp not updated: {e}")

    def update_now(self):
        """what `vts update` does, waited for: the rules, then yt-dlp"""
        if self.cfg.offline:
            self.say("Not with --offline", True)
            return
        said, bad = [], False
        prog = os.path.basename(self.cfg.downloader)
        self.busy("Updating the description of YouTube's pages...")
        rev = self.yt.rules.revision
        try:
            new = update.update_rules(rev)
            if new:
                self.yt.set_rules(new)
                said.append(f"YouTube's pages: revision {new.revision}")
            else:
                said.append("YouTube's pages: up to date")
        except (update.UpdateError, BadRules) as e:
            said.append(f"YouTube's pages: {e}")
            bad = True
        update.done("rules")
        self.busy(f"Updating {prog}...")
        try:
            path, status, out = update.ytdlp_update(self.cfg.downloader)
            lines = [line for line in out.splitlines() if line.strip()]
            if status:
                said.append(f"{prog}: it failed")
                bad = True
            else:
                said.append(lines[-1] if lines else f"{prog}: done")
        except FileNotFoundError:
            said.append(f"{prog} is not installed")
            bad = True
        except PermissionError:
            said.append(f"{prog}: the system's, its packages update it")
        except OSError as e:
            said.append(f"{prog}: {e.strerror}")
            bad = True
        update.done("yt-dlp")
        self.say("; ".join(said), bad, DONE_MESSAGE_TIME)

    def confirm_quit(self):
        """downloads under way: quit anyway?"""
        state = self.downloads[0].state()
        if len(self.downloads) > 1:
            q = (f"{len(self.downloads)} downloads are not finished "
                 f"({state}...): stop them and quit?")
        else:
            q = f"A download is not finished ({state}): stop it and quit?"
        return self.confirm(q)

    def run(self):
        from vts.tui import screens as S
        self.root(S.home(self))
        # say now, not after a video failed, when nothing can play
        problem = update.can_play(self.cfg.player, self.cfg.downloader)
        if problem:
            self.say(problem, True, STARTUP_MESSAGE_TIME)
        self.start_updates()
        try:
            self._loop()
        except KeyboardInterrupt:
            pass
        self.hide_thumb()

    def _loop(self):
        while not self.quit:
            self.draw()
            key = read_key(THUMB_TICK if self.thumbs and self.thumbs.busy()
                           else TICK)
            if key == "INTERRUPT":
                break
            self.poll_player()
            self.poll_downloads()
            if self.thumbs:
                self.thumbs.poll()
            if key:
                try:
                    self.on_key(key)
                except LibError as e:
                    self.failed(e)
                except Exception as e:  # noqa: BLE001 the app goes on
                    import traceback
                    log.err("".join(traceback.format_exception(e)))
                    self.say(f"Something went wrong ({e!r}), in "
                             "~/.cache/vts/vts.log", True)
            # quitting would stop them
            if self.quit:
                self.poll_downloads()  # one may have just ended
                if self.downloads and not self.confirm_quit():
                    self.quit = False


def run(cfg):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        log.err("the full screen app needs a terminal; see 'vts --help' for "
                "commands")
        return 1
    try:
        lib = Library()
    except (LibError, OSError) as e:
        log.err(f"the library: {e}")
        return 1
    yt = Client(cfg)
    # messages go to a file while the screen is ours
    d = platform.cache_dir()
    if d:
        try:
            platform.mkdirs(d)
            log.set_file(os.path.join(d, "vts.log"))
        except OSError:
            pass
    app = App(cfg, lib, yt)
    if cfg.thumbnails != "no":
        from vts import thumbs
        try:
            app.thumbs = thumbs.Thumbs(cfg.thumbnails)
            log.dbg(f"thumbnails: {app.thumbs.mode}")
        except thumbs.Unavailable as e:
            log.dbg(f"thumbnails: {e}")
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(_main, app)
    finally:
        if app.player:
            app.player.finish()
        for job in app.downloads:
            job.dl.cancel()
            job.dl.wait(5)
        if app.thumbs:
            app.thumbs.close()
        lib.close()
        log.close()
    return 0


def _main(scr, app):
    D.scr = scr
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.cbreak()
    curses.noecho()
    try:
        curses.nonl()
    except curses.error:
        pass
    scr.keypad(True)
    mask = curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED | \
        getattr(curses, "BUTTON4_PRESSED", 0) | \
        getattr(curses, "BUTTON5_PRESSED", 0)
    curses.mousemask(mask)
    D.colours()
    app.cols = curses.COLS
    app.run()

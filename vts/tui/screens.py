# SPDX-License-Identifier: Apache-2.0
"""
The screens of the app: Home, Search, a video, its comments, a channel,
a playlist, the Library (your playlists, the history, the downloads),
the Favourites and the Settings.
"""

import curses
import os

from vts import channel as ch
from vts import check, config, platform, sites, util
from vts import search as search_mod
from vts import video as video_mod
from vts.library import WATCH_LATER, Download, LibError
from vts.rules import Item, Page, valid_channel_id
from vts.tui import draw as D
from vts.tui.app import ENTER_KEYS, DownloadJob
from vts.youtube import YouTubeError

HOME_RECENT = 200
HOME_CONTINUE = 5
DESCRIPTION_LINES = 6
COMMENT_LINES = 6


class Screen:
    hints = None
    draws_entries = False  # entry_draw instead of the usual way

    def __init__(self, title):
        self.title = title
        self.items = []
        self.labels = {}  # index -> a label shown above that entry
        self.sel = 0
        self.top = 0
        self.done = False  # no more pages
        self.page = Page()

    def set_page(self, page):
        self.page = page
        self.items = page.items

    def header(self, app, y):
        """draw what isn't the list, from line @y; the next line"""
        return y

    def key(self, app, key):
        """a key the screen handles itself: whether it did"""
        return False

    def more(self, app):
        """the next page of the list: False when there is none"""
        return False

    def reload(self, app):
        pass

    def entry_rows(self, app, i):
        return 2

    def selected(self):
        return self.items[self.sel] if self.items else None


# --- Home --------------------------------------------------------------------


class Home(Screen):
    hints = ("Enter open  p play  w later  r new videos  / search  "
             "4 settings  ? help  q quit")

    def load(self, app):
        self.items = []
        self.labels = {}
        cont = 0
        try:
            for w in app.lib.hist_list(50, "youtube"):
                if cont >= HOME_CONTINUE:
                    break
                if w.watched or w.position <= 0:
                    continue
                it = w.item
                it.f["published"] = (f"stopped at "
                                     f"{util.format_time(w.position)} of "
                                     f"{util.format_time(w.length)}")
                self.items.append(it)
                cont += 1
            if cont:
                self.labels[0] = "Continue watching"
            recent = app.lib.seen_recent(HOME_RECENT)
        except LibError as e:
            app.failed(e)
            recent = Page()
        if recent.items:
            self.labels[len(self.items)] = "New from your favourites"
            self.items += recent.items
        # an empty Home says why in its header, not "Nothing here"
        self.done = bool(self.items)

    def header(self, app, y):
        if self.items:
            return y
        favs = app.lib.favs()
        D.text(y + 1, 2, "Nothing new yet: r to look for new videos." if favs
               else "No favourite channels yet.", app.cols - 4,
               curses.A_BOLD)
        if not favs:
            D.text(y + 3, 2, "Search (/), open a channel, and press f to "
                   "keep it.", app.cols - 4)
            D.text(y + 4, 2, "Or import your subscriptions from Google "
                   "Takeout: 2, then i.", app.cols - 4)
        return y + 6

    def reload(self, app):
        """r on Home: look at the favourites' feeds, then show what is new"""
        app.busy("Looking for new videos...")
        try:
            c = check.run(app.yt, app.lib)
            n = len(c.fresh.items)
            app.say(f"{n} new video{'' if n == 1 else 's'}"
                    + (" (some feeds couldn't be read)" if c.failed else ""),
                    bool(c.failed))
        except LibError as e:
            app.failed(e)
        self.load(app)


def home(app):
    s = Home("Home")
    s.load(app)
    return s


class SiteHome(Screen):
    """
    Twitch's and SOOP's Home: their favourite channels, those live now
    first (with their streams), the others greyed
    """
    hints = ("Enter play  Q quality  a sound  d record  - remove  / find  "
             "b next site  r again")

    def __init__(self, site):
        super().__init__("Home")
        self.site = site
        self.failed = False

    def load(self, app):
        self.items = []
        self.labels = {}
        self.failed = False
        try:
            favs = app.lib.favs(self.site)
        except LibError as e:
            app.failed(e)
            favs = []
        # an empty Home says why in its header, not "Nothing here"
        self.done = bool(favs)
        if not favs:
            return
        name = sites.NAMES[self.site]
        app.busy(f"Who is live on {name}...")
        try:
            got = sites.get(self.site).status([f.id for f in favs])
        except sites.SiteError as e:
            app.failed(e)
            self.failed = True
            got = []
        live = sorted(((c, s) for c, s in got if s),
                      key=lambda cs: -_viewers(cs[1]))
        offline = [c for c, s in got if not s]
        known = {c["id"] for c, _ in got}
        for f in favs:
            if f.id not in known:
                # gone from the site, or it couldn't be asked
                offline.append(Item("channel", id=f.id, title=f.name,
                                    handle=f.handle))
        for c in offline:
            c.f["offline"] = True
        if live:
            self.labels[0] = "Live now"
        self.items = [s for _, s in live]
        # SOOP's past broadcasts stopped in the middle
        cont = self.continuing(app)
        if cont:
            self.labels[len(self.items)] = "Continue watching"
            self.items += cont
        if offline:
            self.labels[len(self.items)] = (
                "Offline" if not self.failed else
                f"Your favourites ({name} didn't answer)")
            self.items += offline

    def continuing(self, app):
        out = []
        try:
            for w in app.lib.hist_list(50, self.site):
                if len(out) >= HOME_CONTINUE:
                    break
                if w.watched or w.position <= 0 or sites.is_live(
                        w.item["id"]):
                    continue
                it = w.item
                it.f["published"] = (f"stopped at "
                                     f"{util.format_time(w.position)} of "
                                     f"{util.format_time(w.length)}")
                out.append(it)
        except LibError as e:
            app.failed(e)
        return out

    def reload(self, app):
        self.load(app)

    def key(self, app, key):
        it = self.selected()
        if key in ("-", "DELETE", "f") and it is not None and (
                it.kind == "channel" or sites.is_live(it["id"])):
            ident = it["id"] if it.kind == "channel" else it["channel_id"]
            app.favourite(ident, it["channel"] or it["title"])
            self.load(app)
            self.sel = min(self.sel, max(0, len(self.items) - 1))
            return True
        return False

    def header(self, app, y):
        if self.items:
            return y
        name = sites.NAMES[self.site]
        D.text(y + 1, 2, f"No {name} favourites yet.", app.cols - 4,
               curses.A_BOLD)
        D.text(y + 3, 2, f"/ finds a {name} channel by its name (or a "
               "link to it), and f keeps it.", app.cols - 4)
        D.text(y + 4, 2, "b goes to the next site (Alt+1 YouTube, Alt+2 "
               "Twitch, Alt+3 SOOP).", app.cols - 4)
        return y + 6


def _viewers(it):
    digits = "".join(c for c in it["views"] or "" if c.isdigit())
    return int(digits) if digits else 0


class SiteSearch(Screen):
    """the channels of Twitch or SOOP whose name is like what was typed"""
    hints = "Enter open  p play  f favourite  b next site  ? help"

    def __init__(self, site, query):
        super().__init__(f"Find: {query}")
        self.site = site
        self.query = query
        self.done = True

    def reload(self, app):
        app.busy(f"Looking for {self.query} on {sites.NAMES[self.site]}...")
        try:
            self.items = sites.get(self.site).find(self.query)
        except sites.SiteError as e:
            app.failed(e)
            self.items = []

    def header(self, app, y):
        if self.items:
            return y
        D.text(y + 1, 2, f"No {sites.NAMES[self.site]} channel found for "
               f"{self.query}.", app.cols - 4, curses.A_BOLD)
        D.text(y + 2, 2, "Its exact name, or a link to it, finds it.",
               app.cols - 4)
        return y + 4


def site_search(app, site, query):
    s = SiteSearch(site, query)
    s.reload(app)
    return s


class SiteChannel(Screen):
    """
    a Twitch or SOOP channel: what it is, its live stream if it's on, and
    (SOOP) its past broadcasts, newest first, more as the list goes down
    """
    hints = "Enter play  Q quality  d record  f favourite  ? help"

    def __init__(self, ident):
        super().__init__(sites.split(ident)[2])
        self.ident = ident
        self.channel = None
        self.done = True
        self.next = None  # the next page of past broadcasts

    def reload(self, app):
        site = sites.site_of(self.ident)
        app.busy(f"Asking {sites.NAMES[site]}...")
        try:
            self.channel, live = sites.get(site).channel(self.ident)
        except sites.SiteError as e:
            app.failed(e)
            return False
        self.title = self.channel["title"]
        self.items = [live] if live else []
        self.labels = {0: "Live now"} if live else {}
        if site == "soop":
            self.hints = ("Enter play  Q quality  d download or record  w "
                          "later  f favourite  ? help")
            try:
                p = sites.get(site).past(self.ident)
            except sites.SiteError as e:
                app.failed(e)
                return True
            if p.items:
                n = f" ({p.estimated:,})" if p.estimated else ""
                self.labels[len(self.items)] = f"Past broadcasts{n}"
                self.items += p.items
            self.next = p.continuation
            self.done = not self.next
        return True

    def more(self, app):
        if not self.next:
            return False
        try:
            p = sites.get(sites.site_of(self.ident)).past(self.ident,
                                                          self.next)
        except sites.SiteError as e:
            app.failed(e)
            return True
        seen = {it["id"] for it in self.items}
        self.items += [it for it in p.items if it["id"] not in seen]
        self.next = p.continuation
        return bool(self.next)

    def key(self, app, key):
        if key == "f" and self.channel:
            app.favourite(self.channel["id"], self.channel["title"],
                          self.channel["handle"])
            return True
        return False

    def header(self, app, y):
        c = self.channel
        if not c:
            return y
        D.text(y, 1, c["title"], app.cols - 2, curses.A_BOLD)
        fav = "★ favourite" if app.lib.fav(c["id"]) else None
        D.text(y + 1, 1, " · ".join(x for x in (
            c["handle"], c["subscribers"], fav) if x), app.cols - 2,
            D.pair(D.C_DIM))
        y += 2
        if c["description"]:
            y += D.wrapped(y, 1, c["description"], app.cols - 2, 3)
        if not self.items or not sites.is_live(self.items[0]["id"]):
            D.text(y + 1, 1, "Not live right now.", app.cols - 2,
                   curses.A_BOLD)
            y += 2
        return y + 1


def site_channel(app, ident):
    s = SiteChannel(ident)
    return s if s.reload(app) else None


def site_home(app, site):
    s = SiteHome(site)
    s.load(app)
    return s


# --- Search ------------------------------------------------------------------

FILTERS = (("Type", "type", search_mod.TYPES), ("Upload date", "date",
                                             search_mod.DATES),
           ("Duration", "duration", search_mod.DURATIONS),
           ("Sort by", "sort", ("relevance", "date", "views", "rating")),
           ("Features", "feature", search_mod.FEATURES))


class Search(Screen):
    hints = ("Enter open  p play  Q quality  d download  w later  "
             "F filters  ? help")

    def __init__(self, query):
        super().__init__(f"Search: {query}")
        self.query = query
        self.opts = search_mod.Options()

    def more(self, app):
        try:
            return search_mod.more(app.yt, self.page)
        except YouTubeError as e:
            app.failed(e)
            return True

    def reload(self, app):
        app.busy("Searching...")
        try:
            self.set_page(search_mod.search(app.yt, self.query, self.opts))
        except YouTubeError as e:
            app.failed(e)
            return False
        self.done = False
        return True

    def filters(self, app):
        what = [f[0] for f in FILTERS] + ["Clear all filters"]
        k = app.choose("Filter", what)
        if k < 0:
            return
        if k == len(FILTERS):
            self.opts = search_mod.Options()
        else:
            name, key, values = FILTERS[k]
            v = app.choose(name, list(values))
            if v < 0:
                return
            self.opts.set(key, values[v])
        self.sel = self.top = 0
        self.reload(app)

    def key(self, app, key):
        if key != "F":
            return False
        self.filters(app)
        return True

    def header(self, app, y):
        if not self.opts.is_default():
            D.text(y, 1, f"Filters: {self.opts.describe()} (F to change)",
                   app.cols - 2, curses.A_DIM)
            y += 1
        if not self.items:
            D.text(y + 1, 2, f"Nothing found for {self.query}.",
                   app.cols - 4, curses.A_BOLD)
            D.text(y + 2, 2, "Other words, fewer filters (F), or a "
                   "channel's @handle or link.", app.cols - 4)
            y += 4
        return y


def search(app, query):
    s = Search(query)
    return s if s.reload(app) else None


# --- a video -----------------------------------------------------------------


class Video(Screen):
    hints = ("p play  Q quality  u subtitles  d download  w later  "
             "C comments  t chapters  Enter up next")

    def __init__(self, ident):
        super().__init__("Video")
        self.ident = ident
        self.v = None
        self.full = False

    def reload(self, app):
        app.busy("Loading the video...")
        try:
            self.v = video_mod.get(app.yt, self.ident)
        except YouTubeError as e:
            app.failed(e)
            return False
        # the list is the videos that come next
        self.set_page(self.v.related)
        self.title = self.v.info.get("title")
        self.done = True
        app.chapters[self.ident] = self.v.chapters
        return True

    def header(self, app, y):
        it = self.v.info
        mx = D.lines() - y - 8 if self.full else DESCRIPTION_LINES
        y += D.wrapped(y, 1, it.get("title"), app.cols - 2, 2, curses.A_BOLD)
        b = it.get("channel")
        if it["subscribers"]:
            b += f" · {it['subscribers']}"
        if it["channel_id"] and app.lib.fav(it["channel_id"]):
            b += " · ★ favourite"
        D.text(y, 1, b, app.cols - 2, curses.A_DIM)
        y += 1
        b = "LIVE · " if "live" in it.f else ""
        b += f"{it.get('views')} · {it.get('date')}"
        if it["likes"]:
            b += f" · {it['likes']} likes"
        if self.v.chapters:
            b += f" · {len(self.v.chapters)} chapters (t)"
        m = app.mark(it)
        if m:
            b += f" · {m}"
        D.text(y, 1, b, app.cols - 2, curses.A_DIM)
        y += 1
        if it["description"] and mx > 0:
            y += 1
            y += D.wrapped(y, 2, it["description"], app.cols - 4, mx)
            if not self.full and D.wrapped_lines(
                    it["description"], app.cols - 4, mx + 1) > mx:
                D.text(y, 2, "... D for all of it", app.cols - 4,
                       curses.A_DIM)
                y += 1
        y += 1
        D.text(y, 1, "Up next" if self.items else "", app.cols - 2,
               curses.A_BOLD)
        return y + 1

    def chapters(self, app):
        cs = self.v.chapters
        if not cs:
            app.say("No chapters")
            return
        now = app.player.current() if app.player else None
        pick = app.choose("Play from", [f"{util.format_time(c.start):>8}  "
                                        f"{c.title}" for c in cs])
        if pick < 0:
            return
        if now and now["id"] == self.ident:
            # it plays already: there
            app.player.command("seek", str(cs[pick].start), "absolute")
            return
        app.subs = ""
        app.no_subs = False
        app.play_with([self.v.info], None, cs[pick].start)

    def key(self, app, key):
        it = self.v.info
        if key == "p":
            app.play([it])
        elif key == "a":
            app.play([it], config.QUALITY_AUDIO)
        elif key == "Q":
            app.play_choose(it)
        elif key == "u":
            app.subtitles(it)
        elif key == "A":
            app.enqueue(it)
        elif key == "w":
            app.watch_later(it)
        elif key == "+":
            app.add_to_list(it)
        elif key == "d":
            app.download(it)
        elif key == "y":
            app.copy_link(it)
        elif key == "f":
            app.favourite(it["channel_id"], it["channel"], it["handle"])
        elif key == "c":
            if valid_channel_id(it["channel_id"]):
                app.push(channel(app, it["channel_id"]))
        elif key == "D":
            self.full = not self.full
        elif key == "t":
            self.chapters(app)
        elif key == "C":
            if not self.v.comments_token:
                app.say("Comments are turned off")
            else:
                app.push(comments(app, self.v.comments_token, "Comments"))
        else:
            return False
        return True


def video(app, ident):
    s = Video(ident)
    return s if s.reload(app) else None


# --- comments ----------------------------------------------------------------


class Comments(Screen):
    hints = "Enter replies  o top/newest  c author's channel  Backspace back"
    draws_entries = True

    def __init__(self, token, title, replies=False, newest=False):
        super().__init__(title)
        self.token = token
        self.replies = replies
        self.newest = newest

    def entry_rows(self, app, i):
        return 2 + D.wrapped_lines(self.items[i].get("text"), app.cols - 6,
                                   COMMENT_LINES)

    def entry_draw(self, app, i, y, rows, selected):
        it = self.items[i]
        D.put(y, 0, "▌" if selected else " ")
        b = "pinned · " if "pinned" in it.f else ""
        b += it.get("channel")
        if "creator" in it.f:
            b += " · creator"
        b += f" · {it.get('published')}"
        if it["likes"] and it["likes"] != "0":
            b += f" · {it['likes']} likes"
        if it["replies"] and it["replies"] != "0":
            b += f" · {it['replies']} replies (Enter)"
        D.text(y, 2, b, app.cols - 3, curses.A_REVERSE | curses.A_BOLD
               if selected else curses.A_BOLD)
        D.wrapped(y + 1, 4, it.get("text"), app.cols - 6, rows - 2)

    def more(self, app):
        if not self.page.continuation:
            return False
        try:
            video_mod.comments_more(app.yt, self.page.continuation, self.page)
        except YouTubeError as e:
            app.failed(e)
        return True

    def reload(self, app):
        app.busy("Loading comments...")
        try:
            if self.replies:
                page = Page()
                video_mod.comments_more(app.yt, self.token, page)
            else:
                page = video_mod.comments_first(app.yt, self.token,
                                                self.newest)
        except YouTubeError as e:
            app.failed(e)
            return False
        self.set_page(page)
        self.done = False
        return True

    def key(self, app, key):
        it = self.selected()
        if key == "o" and not self.replies:
            self.newest = not self.newest
            self.sel = self.top = 0
            self.reload(app)
            app.say("Newest first" if self.newest else "Top comments")
            return True
        if key in ENTER_KEYS:
            if it and it["replies_token"]:
                app.push(comments(app, it["replies_token"], "Replies", True))
            return True
        if key == "c" and it and valid_channel_id(it["channel_id"]):
            app.push(channel(app, it["channel_id"]))
            return True
        # nothing to play or keep in a comment
        return key in ("p", "P", "a", "w", "d", "+", "y", "f", "A", "Q")

    def header(self, app, y):
        if self.page.estimated:
            D.text(y, 1, self.page.estimated, app.cols - 2, curses.A_BOLD)
            y += 1
        return y


def comments(app, token, title, replies=False):
    s = Comments(token, title, replies, app.cfg.comments == "newest")
    return s if s.reload(app) else None


# --- a channel ---------------------------------------------------------------

TAB_NAMES = ("Videos", "Shorts", "Live", "Playlists", "Search")


class Channel(Screen):
    hints = ("Tab next tab  / search it  f favourite  Enter open  p play  "
             "P play all")

    def __init__(self, ident):
        super().__init__("Channel")
        self.ident = ident
        self.tab = "videos"
        self.query = None
        self.info = Item("channel")

    def reload(self, app):
        app.busy("Loading the channel...")
        try:
            b = ch.get(app.yt, self.ident, self.tab, self.query)
        except YouTubeError as e:
            app.failed(e)
            return False
        self.info = b.info
        self.b = b
        self.set_page(b.page)
        self.title = self.info.get("title")
        self.done = False
        return True

    def more(self, app):
        try:
            return ch.more(app.yt, "channel", self.b)
        except YouTubeError as e:
            app.failed(e)
            return True

    def header(self, app, y):
        D.text(y, 1, self.info.get("title"), app.cols - 2, curses.A_BOLD)
        y += 1
        b = self.info.get("handle")
        for k in ("subscribers", "videos"):
            if self.info[k]:
                b += f" · {self.info[k]}"
        if app.lib.fav(self.ident):
            b += " · ★ favourite"
        D.text(y, 1, b, app.cols - 2, curses.A_DIM)
        y += 2
        x = 1
        for t, name in zip(ch.TABS, TAB_NAMES):
            if t == "search" and self.tab != t:
                continue
            D.text(y, x, name, app.cols - x, curses.A_REVERSE | curses.A_BOLD
                   if t == self.tab else 0)
            x += len(name) + 2
        return y + 2

    def key(self, app, key):
        if key in ("\t", "]", "[", "BTAB"):
            n = 4
            t = 0 if self.tab == "search" else ch.TABS.index(self.tab)
            t = (t + n - 1) % n if key in ("[", "BTAB") else (t + 1) % n
            self.tab = ch.TABS[t]
            self.sel = self.top = 0
            self.reload(app)
        elif key == "/":
            q = app.prompt("Search the channel", self.query or "")
            if q:
                self.query = q
                self.tab = "search"
                self.sel = self.top = 0
                self.reload(app)
        elif key == "f":
            app.favourite(self.ident, self.info["title"], self.info["handle"])
        else:
            return False
        return True


def channel(app, ident):
    if not valid_channel_id(ident):
        return None
    s = Channel(ident)
    return s if s.reload(app) else None


# --- a playlist --------------------------------------------------------------


class Playlist(Screen):
    hints = "Enter open  p play  P play from here on  w later  ? help"

    def __init__(self, ident):
        super().__init__("Playlist")
        self.ident = ident
        self.info = Item("playlist")

    def reload(self, app):
        app.busy("Loading the playlist...")
        try:
            self.b = ch.playlist(app.yt, self.ident)
        except YouTubeError as e:
            app.failed(e)
            return False
        self.info = self.b.info
        self.set_page(self.b.page)
        self.title = self.info.get("title")
        self.done = False
        return True

    def more(self, app):
        try:
            return ch.more(app.yt, "playlist", self.b)
        except YouTubeError as e:
            app.failed(e)
            return True

    def header(self, app, y):
        D.text(y, 1, self.info.get("title"), app.cols - 2, curses.A_BOLD)
        y += 1
        b = self.info.get("channel")
        for k in ("videos", "views"):
            if self.info[k]:
                b += f" · {self.info[k]}"
        D.text(y, 1, b, app.cols - 2, curses.A_DIM)
        return y + 2


def playlist(app, ident):
    s = Playlist(ident)
    return s if s.reload(app) else None


# --- the library -------------------------------------------------------------


class Local(Screen):
    """one of your playlists, or the history"""
    hints = "Enter open  p play  P play all from here  - remove  ? help"

    def __init__(self, name, history=False):
        super().__init__(name)
        self.name = name
        self.history = history

    def reload(self, app):
        self.done = True
        try:
            if not self.history:
                self.set_page(app.lib.list_items(self.name))
                return
            items = []
            for w in app.lib.hist_list(500):
                it = w.item
                it.f["published"] = ("watched" if w.watched else
                                     f"stopped at "
                                     f"{util.format_time(w.position)}")
                items.append(it)
            self.items = items
        except LibError as e:
            app.failed(e)

    def key(self, app, key):
        it = self.selected()
        if key in ("-", "DELETE") and it:
            try:
                if self.history:
                    app.lib.hist_remove(it["id"])
                else:
                    app.lib.list_remove(self.name, it["id"])
            except LibError as e:
                app.failed(e)
            app.marks.pop(it["id"], None)
            self.reload(app)
            if self.sel and self.sel >= len(self.items):
                self.sel -= 1
            return True
        if key == "X" and self.history and app.confirm(
                "Clear the whole history?"):
            app.lib.hist_clear()
            app.marks.clear()
            self.reload(app)
            self.sel = 0
            return True
        return key == "w" and not self.history and self.name == WATCH_LATER


def local(app, name, history=False):
    s = Local(name, history)
    s.reload(app)
    return s


class Downloads(Screen):
    """what is downloading, and what was downloaded"""
    hints = ("Enter play the file  - stop or remove from the list  "
             "o where  ? help")

    def __init__(self):
        super().__init__("Downloads")

    def reload(self, app):
        self.done = True
        self.items = []
        self.labels = {}
        if app.downloads:
            self.labels[0] = "Under way"
        for job in app.downloads:
            self.items.append(Item("download", id=job.item["id"],
                                   title=job.title, details=(
                                       f"{job.label} · {job.state()}"),
                                   job=None))
            self.items[-1].job = job
        try:
            done = app.lib.downloads()
        except LibError as e:
            app.failed(e)
            done = []
        if done:
            self.labels[len(self.items)] = "Downloaded"
        for d in done:
            gone = "" if os.path.exists(d.path) else " · the file is gone"
            it = Item("download", id=d.item["id"], title=d.item.get("title"),
                      details=f"{d.path}{gone}")
            it.data = d
            self.items.append(it)

    def header(self, app, y):
        # the progress moves on
        for it in self.items:
            if isinstance(it.data, DownloadJob):
                it.f["details"] = f"{it.data.label} · {it.data.state()}"
        if any(isinstance(it.data, DownloadJob) and
               it.data not in app.downloads for it in self.items):
            sel = self.sel
            self.reload(app)
            self.sel = min(sel, max(0, len(self.items) - 1))
        return y

    def key(self, app, key):
        it = self.selected()
        job = it.data if it and isinstance(it.data, DownloadJob) else None
        saved = it.data if it and isinstance(it.data, Download) else None
        if key in ENTER_KEYS or key == "p":
            if saved:
                app.play_file(saved.path, saved.item.get("title"))
            elif job:
                app.say("Not finished yet")
            return True
        if key in ("-", "DELETE") and it:
            if job and app.confirm("Stop this download?"):
                job.dl.cancel()
            elif saved and app.confirm("Remove it from the list? (the "
                                       "file stays)"):
                app.lib.download_remove(saved.id)
                self.reload(app)
                self.sel = min(self.sel, max(0, len(self.items) - 1))
            return True
        if key == "o" and it:
            app.say(f"In {job.folder if job else os.path.dirname(saved.path)}")
            return True
        if key == "y" and saved:
            app.say(saved.path)
            return True
        # the videos they are: their page
        if key == "c":
            return True
        return key in ("a", "P", "w", "d", "+", "f", "A", "Q", "u")


class Library(Screen):
    hints = "Enter open  N new playlist  R rename  - delete  ? help"

    def __init__(self):
        super().__init__("Library")

    def reload(self, app):
        self.items = []
        self.labels = {}
        self.done = True
        try:
            lists = app.lib.lists()
        except LibError as e:
            app.failed(e)
            return
        self.labels[0] = "Playlists"
        for pl in lists:
            self.items.append(Item("playlist", title=pl.name, videos=(
                f"{pl.count} video{'' if pl.count == 1 else 's'}")))
        self.labels[len(self.items)] = ""
        n = len(app.downloads)
        self.items.append(Item("playlist", title="Downloads", videos=(
            f"{n} under way" if n else "what you downloaded")))
        self.items.append(Item("playlist", title="History",
                               videos="what you watched"))

    def key(self, app, key):
        it = self.selected()
        special = it and self.sel >= len(self.items) - 2
        if key in ENTER_KEYS:
            if not it:
                return True
            if self.sel == len(self.items) - 1:
                app.push(local(app, "History", True))
            elif self.sel == len(self.items) - 2:
                s = Downloads()
                s.reload(app)
                app.push(s)
            else:
                app.push(local(app, it["title"]))
            return True
        if key == "N":
            name = app.prompt("Name of the new playlist")
            if name:
                try:
                    app.lib.list_create(name)
                except LibError as e:
                    app.failed(e)
            self.reload(app)
            return True
        if key in ("-", "DELETE"):
            if it and not special and it["title"] != WATCH_LATER and \
                    app.confirm("Delete this playlist?"):
                app.lib.list_delete(it["title"])
                self.reload(app)
                if self.sel >= len(self.items) and self.sel:
                    self.sel -= 1
            return True
        if key == "R":
            if it and not special and it["title"] != WATCH_LATER:
                name = app.prompt("New name", it["title"])
                if name:
                    try:
                        app.lib.list_rename(it["title"], name)
                    except LibError as e:
                        app.failed(e)
            self.reload(app)
            return True
        # nothing to play or download in a list of lists
        return key in ("p", "P", "a", "w", "d", "+", "y", "c", "f", "A", "Q")


def library(app):
    s = Library()
    s.reload(app)
    return s


# --- favourites --------------------------------------------------------------


class Favourites(Screen):
    """the favourite channels of a site, in their groups"""
    hints = ("Enter channel  N add  n notifications  S Shorts  g group  "
             "- remove  i import  e export")
    site_hints = "Enter channel  N add  g group  - remove  ? help"

    def __init__(self, site="youtube"):
        super().__init__("Favourites")
        self.site = site
        if site != "youtube":
            self.hints = self.site_hints

    def reload(self, app):
        self.items = []
        self.labels = {}
        self.done = True
        group = None
        try:
            favs = app.lib.favs(self.site)
        except LibError as e:
            app.failed(e)
            return
        yt = self.site == "youtube"
        for fv in favs:
            if group is None or group != fv.group:
                self.labels[len(self.items)] = fv.group or "Favourites"
                group = fv.group
            self.items.append(Item(
                "channel", id=fv.id, title=fv.name, handle=fv.handle,
                subscribers=(("notified" if fv.notify else "not notified")
                             + ("" if fv.shorts else ", no Shorts"))
                if yt else None))

    def header(self, app, y):
        if self.items:
            return y
        if self.site != "youtube":
            name = sites.NAMES[self.site]
            D.text(y + 1, 2, f"No {name} favourites yet.", app.cols - 4,
                   curses.A_BOLD)
            D.text(y + 3, 2, f"N adds a {name} channel (its name or a link);"
                   " / finds one, and f keeps it.", app.cols - 4)
            return y + 5
        D.text(y + 1, 2, "No favourite channels yet.", app.cols - 4,
               curses.A_BOLD)
        D.text(y + 3, 2, "Search (/), open a channel, and press f to keep "
               "it;", app.cols - 4)
        D.text(y + 4, 2, "or i to import the subscriptions.csv of Google "
               "Takeout.", app.cols - 4)
        return y + 6

    def key(self, app, key):
        it = self.selected()
        yt = self.site == "youtube"
        if key in ("-", "DELETE", "f"):
            if it:
                app.favourite(it["id"], it["title"])
        elif not yt and key in ("n", "S", "e", "i"):
            # YouTube's: notifications, Shorts, its files
            return True
        elif key in ("n", "S"):
            fav = app.lib.fav(it["id"]) if it else None
            if fav:
                if key == "n":
                    app.lib.fav_set(fav.id, "notify",
                                    "no" if fav.notify else "yes")
                else:
                    app.lib.fav_set(fav.id, "shorts",
                                    "no" if fav.shorts else "yes")
        elif key == "g":
            fav = app.lib.fav(it["id"]) if it else None
            if fav:
                # a blank group takes the channel out of its group
                g = app.prompt("Group (a space for none)", fav.group)
                if g is not None:
                    app.lib.fav_set(fav.id, "group", g.strip(" \t"))
        elif key == "N":
            self.add(app)
        elif key == "e":
            self.export(app)
        elif key == "i":
            name = app.prompt("Subscriptions file (Takeout CSV or OPML)")
            if name:
                name = os.path.expanduser(name)
                try:
                    data = platform.read_file(name, 16 << 20, binary=True)
                    n = app.lib.fav_import(data)
                    app.say(f"{n} channel{'' if n == 1 else 's'} added")
                except (OSError, ValueError):
                    app.say(f"can't read {name}", True)
                except LibError as e:
                    app.failed(e)
        else:
            return key in ("p", "P", "a", "w", "d", "+", "y", "c", "A", "Q")
        self.reload(app)
        if self.sel >= len(self.items) and self.sel:
            self.sel = len(self.items) - 1
        return True


    def add_site(self, app):
        """a Twitch or SOOP channel named by its name, id or link; when
        the name isn't exact, what it finds, to keep one with f"""
        name = sites.NAMES[self.site]
        ref = (app.prompt(f"Add the {name} channel (its name or a link)")
               or "").strip()
        if not ref:
            return
        site = sites.get(self.site)
        ident = site.link(ref)
        app.busy(f"Looking for {ref} on {name}...")
        try:
            if ident and sites.valid_channel_id(ident):
                c, _ = site.channel(ident)
            else:
                found = [c for c in site.find(ref) if c.kind == "channel"]
                c = found[0] if found and ref.lower() in (
                    str(found[0]["handle"]).lower(),
                    str(found[0]["title"]).lower()) else None
                if c is None:
                    app.push(site_search(app, self.site, ref))
                    app.say(f"No {name} channel named exactly {ref}: f keeps "
                            "one of these")
                    return
        except sites.SiteError as e:
            app.failed(e)
            return
        if app.lib.fav(c["id"]):
            app.say(f"{c['title']} is already a favourite")
            return
        app.favourite(c["id"], c["title"], c["handle"])

    def add(self, app):
        """a channel named by its @handle, id or link"""
        from vts.cli.library import add_favourite
        if self.site != "youtube":
            self.add_site(app)
            return
        ref = app.prompt("Add the channel (@handle, link or id)")
        ref = (ref or "").strip()
        if not ref:
            return
        app.busy(f"Looking for {ref}...")
        try:
            ident = ch.resolve(app.yt, ref)
            name, new = add_favourite(app.lib, app.yt, ident)
        except (YouTubeError, LibError) as e:
            app.failed(e)
            return
        app.say(f"{name} is a favourite: its new videos will be on Home"
                if new else f"{name} is already a favourite")

    def export(self, app):
        """the favourites into a file: OPML, or CSV when it ends in .csv"""
        try:
            folder = app.cfg.downloads_dir() or ""
        except OSError:
            folder = ""
        name = app.prompt("Export to (OPML, or CSV if it ends in .csv)",
                          os.path.join(folder, "vts-favourites.opml"))
        name = os.path.expanduser((name or "").strip())
        if not name:
            return
        try:
            data = app.lib.fav_export(opml=not name.lower().endswith(".csv"))
            platform.write_atomic(name, data, 0o644)
        except LibError as e:
            app.failed(e)
            return
        except OSError as e:
            app.say(f"{name}: {e.strerror}", True)
            return
        app.say(f"Favourites written to {name}")


def favourites(app, site="youtube"):
    s = Favourites(site)
    s.reload(app)
    return s


# --- settings ----------------------------------------------------------------


class Settings(Screen):
    """the settings, what they do and their values; changed in place"""
    hints = "Enter change  d defaults for it  ? help  q quit"

    def __init__(self):
        super().__init__("Settings")

    def reload(self, app):
        from vts.cli import upkeep
        self.done = True
        self.items = []
        self.labels = {}
        self.path = config.default_path(app.cfg)
        # General first, then each site's
        for section in config.SECTIONS:
            self.labels[len(self.items)] = section
            for s in config.SETTINGS:
                if s.section != section:
                    continue
                v = app.cfg.get(s.key)
                it = Item("setting", id=s.key, title=f"{s.key} = {v}"
                          if v != "" else f"{s.key} =   ({s.default})",
                          details=f"{s.values}")
                it.data = s
                self.items.append(it)
        self.labels[len(self.items)] = "New videos in the background"
        timer = upkeep.timer_installed()
        it = Item("action", id="timer", title=(
            "Checked every 30 minutes: on" if timer else
            "Checked in the background: off"), details=(
            f"{timer}; Enter turns it off" if timer else
            "Enter checks every 30 minutes, with notifications"))
        self.items.append(it)
        self.labels[len(self.items)] = "Does everything work?"
        self.items.append(Item("action", id="doctor", title="Check now",
                               details="what vts doctor does: the programs, "
                               "YouTube, the library"))
        self.items.append(Item("action", id="update", title="Update now",
                               details="what vts update does: the latest "
                               "description of YouTube's pages, and "
                               f"{app.cfg.downloader}'s latest version (done "
                               "on its own too, with updates = yes)"))

    def header(self, app, y):
        it = self.selected()
        where = self.path or "no settings file (no home folder)"
        home = platform.home()
        if self.path and home and not platform.WINDOWS and \
                self.path.startswith(home.rstrip("/") + "/"):
            where = "~" + self.path[len(home.rstrip("/")):]
        D.text(y, 1, f"Saved in {where}", app.cols - 2, curses.A_DIM)
        y += 1
        about = ""
        if it is not None and isinstance(it.data, config.Setting):
            about = it.data.about + f" Default: {it.data.default}."
        elif it is not None:
            about = it.get("details")
        n = D.wrapped(y, 1, about, app.cols - 2, 4)
        return y + max(n, 3)

    def change(self, app, s):
        cur = app.cfg.get(s.key)
        if s.choices:
            choices = list(s.choices)
            i = app.choose(f"{s.key}", choices,
                           choices.index(cur) if cur in choices else 0)
            if i < 0:
                return
            value = choices[i]
        else:
            value = app.prompt(f"{s.key} (a space for the default)", cur)
            if value is None:
                return
            value = value.strip()
        self.save(app, s.key, value)

    def save(self, app, key, value):
        """@key set to @value (None, or nothing typed: the default)"""
        if not self.path:
            app.say("no home folder: nowhere to keep the settings", True)
            return
        attr = config.setting(key).attr
        old = getattr(app.cfg, attr)
        try:
            if value is None or (value == "" and key not in (
                    "region", "subtitles", "downloads")):
                value = None
                setattr(app.cfg, attr, getattr(config.Config(), attr))
            else:
                app.cfg.set(key, value)
        except config.BadValue as e:
            app.say(f"{key}: {e}", True)
            return
        try:
            config.edit_file(self.path, key, None if value is None
                             else app.cfg.get(key))
        except OSError as e:
            setattr(app.cfg, attr, old)
            app.say(f"{self.path}: {e.strerror}", True)
            return
        self.apply(app, key)
        v = app.cfg.get(key)
        app.say(f"{key} = {v}" + (" (the default)" if value is None else "")
                if v != "" else f"{key}: the default")

    def apply(self, app, key):
        """what a new value changes right away"""
        if key in ("language", "region"):
            app.yt.hl = app.cfg.language
            app.yt.gl = app.cfg.region
        elif key == "thumbnails":
            from vts import thumbs
            app.hide_thumb()
            if app.thumbs:
                app.thumbs.close()
            app.thumbs = None
            if app.cfg.thumbnails != "no":
                try:
                    app.thumbs = thumbs.Thumbs(app.cfg.thumbnails)
                except thumbs.Unavailable as e:
                    app.say(f"No thumbnails: {e}", True)

    def key(self, app, key):
        from vts.cli import upkeep
        it = self.selected()
        if key in ENTER_KEYS and it is not None:
            if isinstance(it.data, config.Setting):
                self.change(app, it.data)
            elif it["id"] == "timer":
                self.timer(app, upkeep)
            elif it["id"] == "doctor":
                app.push(doctor(app))
                return True
            elif it["id"] == "update":
                app.update_now()
            sel = self.sel
            self.reload(app)
            self.sel = sel
            return True
        if key == "d" and it is not None and isinstance(it.data,
                                                        config.Setting):
            self.save(app, it.data.key, None)
            sel = self.sel
            self.reload(app)
            self.sel = sel
            return True
        return key in ("p", "P", "a", "w", "+", "y", "c", "f", "A", "Q", "u")

    def timer(self, app, upkeep):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            if upkeep.timer_installed():
                status = upkeep.uninstall()
            else:
                status = upkeep.install(30, app.cfg.config_file)
        msg = out.getvalue().strip()
        app.say(msg or ("done" if not status else "it couldn't be done: "
                        "vts check --install says why"), bool(status))


def settings(app):
    s = Settings()
    s.reload(app)
    return s


class Doctor(Screen):
    hints = "r again  Backspace back"
    marks = ("ok", "--", "!!", "XX")

    def __init__(self):
        super().__init__("Check")

    def reload(self, app):
        from vts.cli import upkeep
        app.busy("Checking...")
        d = upkeep.run_doctor(app.cfg)
        self.items = [Item("doctor", id=what, title=f"{self.marks[v]}  "
                           f"{what:<12} {text}", details="")
                      for v, what, text in d.lines]
        self.summary = d.summary()
        self.done = True

    def entry_rows(self, app, i):
        return 1

    def header(self, app, y):
        D.text(y, 1, self.summary, app.cols - 2, curses.A_BOLD)
        return y + 2

    def key(self, app, key):
        return key in ("p", "P", "a", "w", "d", "+", "y", "c", "f", "A", "Q",
                       "u") or key in ENTER_KEYS


def doctor(app):
    s = Doctor()
    s.reload(app)
    return s

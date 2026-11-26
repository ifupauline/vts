# SPDX-License-Identifier: Apache-2.0
"""
Twitch: its channels and who of them is live, through Twitch's public
GraphQL interface (what its website uses; no account). How to ask is in
vts/twitch.json, kept up to date like youtube.json. Only live streams:
past broadcasts and clips are left out.
"""

import datetime
import json
import os
import urllib.parse

from vts import http
from vts.rules import Item, Page
from vts.sites import (Site, SiteError, category_id, channel_id, live_id,
                       split, twitch_login, valid_category_id)
from vts.text import clean

DESC_MAX = 64 * 1024
ANSWER_MAX = 4 * 1024 * 1024


def load_description(path=None):
    """twitch.json: the one given, else the one built into vts"""
    path = path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "twitch.json")
    with open(path, "rb") as f:
        data = f.read(DESC_MAX + 1)
    if len(data) > DESC_MAX:
        raise ValueError("twitch.json: too big")
    d = json.loads(data)
    if not isinstance(d.get("queries"), dict) or not isinstance(
            d.get("client_id"), str) or not str(d.get("url", "")).startswith(
                "https://"):
        raise ValueError("twitch.json: not a description of Twitch")
    return d


def on_for(created, now=None):
    """ "on for 2 h 13" since @created (2026-11-24T04:06:40Z), or None"""
    try:
        t = datetime.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    t = t.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    m = int((now - t).total_seconds() // 60)
    if m < 0:
        return None
    if m < 60:
        return f"on for {m} min"
    return f"on for {m // 60} h {m % 60:02d}"


def _count(n, what):
    try:
        return f"{int(n):,} {what}"
    except (TypeError, ValueError):
        return None


def _https(url):
    return url if isinstance(url, str) and url.startswith("https://") \
        else None


def channel_item(u):
    """a Twitch user (GraphQL) as a channel Item"""
    login = u.get("login")
    if not twitch_login(login):
        return None
    it = Item("channel", id=channel_id("twitch", login),
              title=clean(u.get("displayName") or login), handle=login,
              subscribers=_count((u.get("followers") or {}).get(
                  "totalCount"), "followers"),
              description=clean(u.get("description") or "") or None,
              thumbnail=_https(u.get("profileImageURL")))
    s = u.get("stream")
    if isinstance(s, dict) and s.get("type", "live") == "live":
        live = live_item(u)
        for k in ("live", "views", "game"):
            if live[k]:
                it.f[k] = live[k]
        it.f["stream_title"] = live["title"]
        if live["thumbnail"]:
            it.f["stream_thumbnail"] = live["thumbnail"]
    return it


def live_item(u):
    """what the Twitch user @u streams now as an Item, or None"""
    login = u.get("login")
    s = u.get("stream")
    if not twitch_login(login) or not isinstance(s, dict) or \
            s.get("type", "live") != "live":
        return None
    name = clean(u.get("displayName") or login)
    return Item("video", id=live_id("twitch", login),
                title=clean(s.get("title") or "") or name, channel=name,
                channel_id=channel_id("twitch", login),
                views=_count(s.get("viewersCount"), "watching"),
                published=on_for(s.get("createdAt")),
                game=clean((s.get("game") or {}).get("displayName") or "")
                or None,
                thumbnail=_https(s.get("previewImageURL")), live=True)


class Twitch(Site):
    key = "twitch"
    name = "Twitch"

    def __init__(self, description=None):
        if description is None:
            from vts import update
            description = update.best_site("twitch")
        self.d = description
        # the tests' own Twitch
        self.url = os.environ.get("VTS_TWITCH_URL") or self.d["url"]

    def _ask(self, query, variables):
        """the data of the answer to the query named @query"""
        text = self.d["queries"].get(query)
        if not text:
            raise SiteError(f"Twitch: no query {query} in twitch.json")
        r = http.fetch(http.Request(
            self.url, json.dumps({"query": text,
                                  "variables": variables}).encode(),
            content_type="application/json",
            headers={"Client-ID": self.d["client_id"]},
            max_size=ANSWER_MAX, retries=1))
        if r.error:
            raise SiteError(f"Twitch: {r.message}")
        if r.status != 200:
            raise SiteError(f"Twitch answered {r.status}")
        try:
            a = json.loads(r.body)
        except ValueError:
            raise SiteError("Twitch: not an answer") from None
        if not isinstance(a, dict) or not isinstance(a.get("data"), dict):
            errors = a.get("errors") if isinstance(a, dict) else None
            why = errors[0].get("message") if isinstance(errors, list) and \
                errors and isinstance(errors[0], dict) else "not an answer"
            raise SiteError(f"Twitch: {clean(str(why))[:200]}")
        return a["data"]

    def users(self, logins):
        """the Twitch users named @logins (those that exist)"""
        logins = [x for x in dict.fromkeys(logins) if twitch_login(x)]
        out = []
        step = int(self.d.get("max_logins", 100))
        for i in range(0, len(logins), step):
            got = self._ask("users", {"logins": logins[i:i + step]})
            out += [u for u in got.get("users") or ()
                    if isinstance(u, dict)]
        return out

    def find(self, query):
        """channels like @query: the one named so first"""
        q = query.strip().lstrip("@").lower()
        found = []
        if twitch_login(q):
            found += self.users([q])
        got = self._ask("search", {"query": query.strip()})
        found += [(e or {}).get("node") or {} for e in
                  ((got.get("searchUsers") or {}).get("edges") or ())]
        items, seen = [], set()
        for u in found:
            it = channel_item(u) if isinstance(u, dict) else None
            if it and it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
        return items

    def channel(self, ident):
        """(the channel, its live stream or None); SiteError if none"""
        login = ident.split(":", 1)[1] if ":" in ident else ident
        users = self.users([login])
        if not users:
            raise SiteError(f"no Twitch channel {login}")
        return channel_item(users[0]), live_item(users[0])

    def status(self, idents):
        """[(channel, live stream or None)], in the order of @idents"""
        logins = [i.split(":", 1)[1] for i in idents if ":" in i]
        by_login = {u.get("login"): u for u in self.users(logins)}
        out = []
        for login in logins:
            u = by_login.get(login)
            c = channel_item(u) if u else None
            if c:
                out.append((c, live_item(u)))
        return out

    def live(self, idents):
        """{channel id: its live stream} of those live now"""
        logins = [i.split(":", 1)[1] for i in idents if ":" in i]
        out = {}
        for u in self.users(logins):
            it = live_item(u)
            if it:
                out[it["channel_id"]] = it
        return out

    def categories(self, query):
        a = self._ask("categories", {"query": query.strip()[:100]})
        out = []
        for e in ((a.get("searchCategories") or {}).get("edges") or ()):
            n = e.get("node") if isinstance(e, dict) else None
            if not isinstance(n, dict) or not str(n.get("id", "")).isdigit():
                continue
            out.append(Item(
                "category", id=category_id("twitch", n["id"]),
                title=clean(n.get("displayName") or n["id"]),
                views=_count(n.get("viewersCount"), "watching"),
                thumbnail=_https(n.get("boxArtURL"))))
        # the one watched most first: "StarCraft" is two, one of them empty
        out.sort(key=lambda it: -_viewers(it["views"]))
        return out

    def category(self, ident, after=None, languages=()):
        if not valid_category_id(ident):
            raise SiteError(f"not a Twitch category: {ident}")
        v = {"id": split(ident)[2], "after": after,
             "languages": [x.upper() for x in languages] or None}
        g = self._ask("category", v).get("game")
        if not isinstance(g, dict):
            raise SiteError("no such Twitch category")
        page = Page()
        streams = g.get("streams") or {}
        cursor = None
        for e in streams.get("edges") or ():
            n = e.get("node") if isinstance(e, dict) else None
            if not isinstance(n, dict):
                continue
            cursor = e.get("cursor") or cursor
            b = n.get("broadcaster") or {}
            it = live_item({"login": b.get("login"),
                            "displayName": b.get("displayName"),
                            "stream": dict(n, game={"displayName": g.get(
                                "displayName")})})
            if it:
                page.add(it)
        more = (streams.get("pageInfo") or {}).get("hasNextPage")
        page.continuation = cursor if more and isinstance(cursor, str) \
            else None
        return page

    def link(self, text):
        """the channel a twitch.tv link names: twitch:LOGIN, or None"""
        t = text.strip()
        if "://" not in t:
            t = "https://" + t
        u = urllib.parse.urlsplit(t)
        host = (u.hostname or "").lower()
        if host not in ("twitch.tv", "www.twitch.tv", "m.twitch.tv"):
            return None
        first = u.path.strip("/").split("/")[0].lower()
        if twitch_login(first) and first not in (
                "directory", "videos", "settings", "search", "p", "downloads",
                "jobs", "turbo", "subscriptions", "inventory", "wallet"):
            return channel_id("twitch", first)
        return None

    def stream_url(self, it):
        from vts.sites import live_url
        return live_url(it["id"])


def _viewers(views):
    digits = "".join(c for c in str(views or "") if c.isdigit())
    return int(digits) if digits else -1

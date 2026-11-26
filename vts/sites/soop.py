# SPDX-License-Identifier: Apache-2.0
"""
SOOP (sooplive.co.kr, AfreecaTV before): its channels, who of them is
live, and their past broadcasts, asked as its website does (no account).
Where to ask is in vts/soop.json, kept up to date like youtube.json.
SOOP closes the connection of what doesn't say it is a browser.
"""

import datetime
import json
import os
import urllib.parse

from vts import http, util
from vts.rules import Item, Page
from vts.sites import (Site, SiteError, category_id, channel_id, live_id,
                       soop_id, split, valid_category_id)
from vts.text import clean

DESC_MAX = 64 * 1024
ANSWER_MAX = 4 * 1024 * 1024


def load_description(path=None):
    """soop.json: the one given, else the one built into vts"""
    path = path or os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "soop.json")
    with open(path, "rb") as f:
        data = f.read(DESC_MAX + 1)
    if len(data) > DESC_MAX:
        raise ValueError("soop.json: too big")
    d = json.loads(data)
    for k in ("search", "station", "past", "live_thumbnail"):
        if not str(d.get(k, "")).startswith("https://"):
            raise ValueError("soop.json: not a description of SOOP")
    return d


def _https(url):
    """SOOP's "//host/x.jpg" as https"""
    if not isinstance(url, str):
        return None
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url if url.startswith("https://") else None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _count(n, what):
    n = _int(n)
    return f"{n:,} {what}" if n is not None else None


def _seconds(kst, tz):
    """seconds since 1970 of SOOP's "2026-11-25 18:38:44" (Korea's time)"""
    if not isinstance(kst, str):
        return None
    return util.iso_time(f"{kst[:19]}+{tz:02d}:00")


def on_for(start, now, tz=9):
    """ "on for 2 h 13" from SOOP's start and now (Korea's time)"""
    a, b = _seconds(start, tz), _seconds(now, tz)
    if a is None or b is None or b < a:
        return None
    m = (b - a) // 60
    return f"on for {m} min" if m < 60 else f"on for {m // 60} h {m % 60:02d}"


class Soop(Site):
    key = "soop"
    name = "SOOP"

    def __init__(self, description=None):
        if description is None:
            from vts import update
            description = update.best_site("soop")
        self.d = description
        self.tz = int(self.d.get("time_zone", 9))
        # the tests' own SOOP: https://HOST/PATH -> TEST/soop/HOST/PATH
        self.test = os.environ.get("VTS_SOOP_URL", "")

    def _url(self, template, **values):
        url = template.format(**{k: urllib.parse.quote(str(v), safe="")
                                 for k, v in values.items()})
        if self.test:
            url = self.test + "/soop/" + url[len("https://"):]
        return url

    def _request(self, url, body=None):
        return http.Request(url, body, headers={
            "User-Agent": self.d.get("user_agent", "Mozilla/5.0")},
            max_size=ANSWER_MAX, retries=1)

    def _answer(self, r):
        if r.error:
            raise SiteError(f"SOOP: {r.message}")
        if r.status != 200:
            raise SiteError(f"SOOP answered {r.status}")
        try:
            a = json.loads(r.body)
        except ValueError:
            raise SiteError("SOOP: not an answer") from None
        if not isinstance(a, dict):
            raise SiteError("SOOP: not an answer")
        return a

    def _get(self, url):
        return self._answer(http.fetch(self._request(url)))

    # --- what SOOP answers, as Items ------------------------------------

    def channel_item(self, a):
        """a station (chapi .../station) as a channel Item"""
        st = a.get("station") if isinstance(a.get("station"), dict) else {}
        user = st.get("user_id")
        if not soop_id(user):
            return None
        upd = st.get("upd") if isinstance(st.get("upd"), dict) else {}
        it = Item("channel", id=channel_id("soop", user),
                  title=clean(st.get("user_nick") or st.get("station_name")
                              or user), handle=user,
                  subscribers=_count(upd.get("fan_cnt"), "fans"),
                  description=clean(st.get("station_title") or "") or None,
                  thumbnail=_https(a.get("profile_image")))
        live = self.live_item(a)
        if live:
            for k in ("live", "views", "locked"):
                if live[k]:
                    it.f[k] = live[k]
            it.f["stream_title"] = live["title"]
            if live["thumbnail"]:
                it.f["stream_thumbnail"] = live["thumbnail"]
        return it

    def live_item(self, a):
        """what a station broadcasts now as an Item, or None"""
        st = a.get("station") if isinstance(a.get("station"), dict) else {}
        b = a.get("broad")
        user = st.get("user_id")
        if not isinstance(b, dict) or not soop_id(user) or \
                _int(b.get("broad_no")) is None:
            return None
        name = clean(st.get("user_nick") or user)
        locked = None
        # what needs a SOOP account (vts has none)
        if b.get("is_password"):
            locked = "with a password"
        elif (_int(b.get("broad_grade")) or 0) >= 19:
            locked = "19+"
        elif b.get("subscription_only"):
            locked = "subscribers only"
        return Item("video", id=live_id("soop", user),
                    title=clean(b.get("broad_title") or "") or name,
                    channel=name, channel_id=channel_id("soop", user),
                    views=_count(b.get("current_sum_viewer"), "watching"),
                    published=on_for(st.get("broad_start"),
                                     a.get("current_timestamp"), self.tz),
                    thumbnail=self._url(self.d["live_thumbnail"],
                                        broad_no=b["broad_no"]),
                    locked=locked, live=True)

    def past_item(self, v, nick=None):
        """a past broadcast (chapi .../vods/review) as an Item"""
        number = _int(v.get("title_no"))
        user = v.get("user_id")
        if number is None or not soop_id(user):
            return None
        ucc = v.get("ucc") if isinstance(v.get("ucc"), dict) else {}
        ms = _int(ucc.get("total_file_duration"))
        count = v.get("count") if isinstance(v.get("count"), dict) else {}
        when = _seconds(v.get("reg_date"), self.tz)
        return Item("video", id=f"soop:vod:{number}",
                    title=clean(v.get("title_name") or ""),
                    channel=clean(v.get("user_nick") or nick or user),
                    channel_id=channel_id("soop", user),
                    duration=util.format_time(ms // 1000) if ms else None,
                    views=_count(count.get("read_cnt"), "views"),
                    published=util.ago(datetime.datetime.fromtimestamp(
                        when, datetime.timezone.utc).isoformat())
                    if when else None,
                    thumbnail=_https(ucc.get("thumb")))

    # --- the Site's questions --------------------------------------------

    def stations(self, idents):
        """{channel id: SOOP's answer} for the channels @idents, asked at
        once (those that couldn't be asked are left out)"""
        names = [i.split(":", 1)[1] for i in idents if ":" in i]
        names = [n for n in dict.fromkeys(names) if soop_id(n)]
        reqs = [self._request(self._url(self.d["station"], id=n))
                for n in names]
        out = {}
        failed = None
        for n, r in zip(names, http.fetch_all(
                reqs, int(self.d.get("parallel", 8)))):
            try:
                a = self._answer(r)
            except SiteError as e:
                failed = e
                continue
            if isinstance(a.get("station"), dict):
                out[channel_id("soop", n)] = a
        if failed and not out:
            raise failed
        return out

    def status(self, idents):
        got = self.stations(idents)
        out = []
        for i in idents:
            a = got.get(i)
            c = self.channel_item(a) if a else None
            if c:
                out.append((c, self.live_item(a)))
        return out

    def live(self, idents):
        return {c["id"]: s for c, s in self.status(idents) if s}

    def channel(self, ident):
        got = self.stations([ident])
        a = got.get(ident)
        c = self.channel_item(a) if a else None
        if not c:
            raise SiteError(f"no SOOP channel {ident.split(':', 1)[-1]}")
        return c, self.live_item(a)

    def categories(self, query):
        a = self._get(self._url(self.d["categories"], query=query.strip()))
        data = a.get("data") if isinstance(a.get("data"), dict) else {}
        out = []
        for c in data.get("list") or ():
            code = str(c.get("category_no", "")) if isinstance(c, dict) \
                else ""
            if not code.isdigit():
                continue
            out.append(Item("category", id=category_id("soop", code),
                            title=clean(c.get("category_name") or code),
                            views=_count(c.get("view_cnt"), "watching"),
                            thumbnail=_https(c.get("cate_img"))))
        return out

    def category(self, ident, after=None, languages=()):
        if not valid_category_id(ident):
            raise SiteError(f"not a SOOP category: {ident}")
        page_no = _int(after) or 1
        a = self._get(self._url(self.d["category"], page=page_no,
                                code=split(ident)[2]))
        data = a.get("data") if isinstance(a.get("data"), dict) else {}
        # SOOP gives when they started, in Korea's time: now in it too
        now = (datetime.datetime.now(datetime.timezone.utc) +
               datetime.timedelta(hours=self.tz)).strftime(
                   "%Y-%m-%d %H:%M:%S")
        page = Page()
        for b in data.get("list") or ():
            if not isinstance(b, dict) or not soop_id(b.get("user_id")):
                continue
            it = self.live_item({
                "station": {"user_id": b["user_id"],
                            "user_nick": b.get("user_nick"),
                            "broad_start": b.get("broad_start")},
                "broad": {"broad_no": b.get("broad_no"),
                          "broad_title": b.get("broad_title"),
                          "current_sum_viewer": b.get("view_cnt"),
                          "is_password": b.get("is_password"),
                          "broad_grade": b.get("grade"),
                          "subscription_only": b.get("subscription_only")},
                "current_timestamp": now})
            if it:
                page.add(it)
        page.continuation = str(page_no + 1) if data.get("is_more") and \
            page.items else None
        return page

    def find(self, query):
        """channels like @query: the one whose id it is first"""
        q = query.strip().lstrip("@")
        items, seen = [], set()
        if soop_id(q.lower()):
            try:
                c, _ = self.channel(channel_id("soop", q.lower()))
                items.append(c)
                seen.add(c["id"])
            except SiteError:
                pass
        try:
            a = self._get(self._url(self.d["search"], query=q))
        except SiteError:
            # the channel named so is found all the same
            if items:
                return items
            raise
        for e in a.get("DATA") or ():
            if not isinstance(e, dict) or not soop_id(e.get("user_id")):
                continue
            ident = channel_id("soop", e["user_id"])
            if ident in seen:
                continue
            seen.add(ident)
            items.append(Item(
                "channel", id=ident,
                title=clean(e.get("user_nick") or e.get("station_name") or
                            e["user_id"]), handle=e["user_id"],
                subscribers=_count(e.get("favorite_cnt"), "fans"),
                description=clean(e.get("station_title") or "") or None,
                thumbnail=_https(e.get("station_logo"))))
        return items

    def past(self, ident, page=1):
        """page @page of the past broadcasts of @ident, newest first"""
        name = ident.split(":", 1)[1]
        a = self._get(self._url(self.d["past"], id=name, page=page))
        p = Page()
        for v in a.get("data") or ():
            it = self.past_item(v) if isinstance(v, dict) else None
            if it:
                p.items.append(it)
        meta = a.get("meta") if isinstance(a.get("meta"), dict) else {}
        last = _int(meta.get("last_page")) or page
        p.continuation = page + 1 if page < last and p.items else None
        p.estimated = _int(meta.get("total"))
        return p

    def link(self, text):
        """the channel or past broadcast a SOOP link names, or None"""
        t = text.strip()
        if "://" not in t:
            t = "https://" + t
        u = urllib.parse.urlsplit(t)
        host = (u.hostname or "").lower()
        parts = [p for p in u.path.split("/") if p]
        if not host.endswith(("sooplive.co.kr", "sooplive.com",
                              "afreecatv.com")) or not parts:
            return None
        sub = host.split(".")[0]
        if sub == "vod" and len(parts) >= 2 and parts[0] == "player" and \
                parts[1].isdigit():
            return f"soop:vod:{int(parts[1])}"
        if sub in ("www", "sooplive") and len(parts) >= 2 and \
                parts[0] == "station":
            name = parts[1].lower()
        elif sub in ("play", "ch", "bj"):
            name = parts[0].lower()
        else:
            return None
        return channel_id("soop", name) if soop_id(name) else None

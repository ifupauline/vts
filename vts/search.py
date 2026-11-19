# SPDX-License-Identifier: Apache-2.0
"""Search, with the website's filters."""

import base64

from vts.rules import Page

SORTS = ("relevance", "rating", "date", "views")
DATES = ("any", "hour", "today", "week", "month", "year")
TYPES = ("any", "video", "shorts", "channel", "playlist", "movie")
DURATIONS = ("any", "short", "medium", "long")
FEATURES = ("live", "4k", "hd", "subtitles", "cc", "360", "vr180", "3d", "hdr",
            "location", "purchased")

VALUES = {
    "sort": "relevance, rating, date, views",
    "date": "any, hour, today, week, month, year",
    "type": "any, video, shorts, channel, playlist, movie",
    "duration": "any, short (under 3 min), medium (3-20 min), long (over 20 "
                "min)",
    "feature": "live, 4k, hd, subtitles, cc, 360, vr180, 3d, hdr, location, "
               "purchased",
}


class Options:
    def __init__(self):
        self.sort = "relevance"
        self.date = "any"
        self.type = "any"
        self.duration = "any"
        self.features = set()

    def set(self, what, value):
        """set a filter; ValueError if @value isn't one of its values"""
        if what == "feature":
            names = value.split(",") if value else []
            if value.endswith(","):
                names = names[:-1]
            for n in names:
                if n not in FEATURES:
                    raise ValueError(value)
            self.features |= set(names)
            return
        table = {"sort": SORTS, "date": DATES, "type": TYPES,
                 "duration": DURATIONS}.get(what)
        if not table or value not in table:
            raise ValueError(value)
        setattr(self, what, value)

    def is_default(self):
        return self.params() == ""

    def describe(self):
        """ "videos, this week, 4k" for people"""
        parts = []
        if self.type != "any":
            parts.append(self.type)
        if self.date != "any":
            parts.append({"hour": "last hour", "today": "today",
                          "week": "this week", "month": "this month",
                          "year": "this year"}[self.date])
        if self.duration != "any":
            parts.append({"short": "under 3 min", "medium": "3-20 min",
                          "long": "over 20 min"}[self.duration])
        parts.extend(f for f in FEATURES if f in self.features)
        if self.sort != "relevance":
            parts.append(f"by {self.sort}")
        return ", ".join(parts)

    def params(self):
        """
        message SearchParams {
          uint32 sort = 1;
          message Filter {
            uint32 date = 1; uint32 type = 2; uint32 duration = 3;
            bool hd = 4; bool subtitles = 5; bool creative_commons = 6;
            bool three_d = 7; bool live = 8; bool purchased = 9;
            bool four_k = 14; bool three_sixty = 15; bool location = 23;
            bool hdr = 25; bool vr180 = 26;
          }
          Filter filters = 2;
        }
        as the website builds it, in base64
        """
        filters = b""
        if self.date != "any":
            # the date filter numbers are the order's own (hour 1 ... year 5)
            filters += _field(1, DATES.index(self.date))
        if self.type != "any":
            filters += _field(2, {"video": 1, "channel": 2, "playlist": 3,
                                  "movie": 4, "shorts": 9}[self.type])
        if self.duration != "any":
            filters += _field(3, {"short": 4, "medium": 5,
                                  "long": 2}[self.duration])
        for name, number in (("hd", 4), ("subtitles", 5), ("cc", 6),
                             ("3d", 7), ("live", 8), ("purchased", 9),
                             ("4k", 14), ("360", 15), ("location", 23),
                             ("hdr", 25), ("vr180", 26)):
            if name in self.features:
                filters += _field(number, 1)
        msg = b""
        if self.sort != "relevance":
            msg += _field(1, SORTS.index(self.sort))
        if filters:
            # field 2, wire type 2: length then bytes
            msg += _varint((2 << 3) | 2) + _varint(len(filters)) + filters
        return base64.b64encode(msg).decode() if msg else ""


def _varint(v):
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        if v:
            out.append(c | 0x80)
        else:
            out.append(c)
            return bytes(out)


def _field(number, v):
    return _varint(number << 3) + _varint(v)  # wire type 0


def search(yt, query, opts=None):
    """the first page of results for @query"""
    fields = {"query": query}
    params = opts.params() if opts else ""
    if params:
        fields["params"] = params
    page = Page()
    return yt.rules.page(yt.call("search", fields), page)


def more(yt, page):
    """the next page, added to @page; False when there is none"""
    if not page.continuation:
        return False
    yt.rules.page(yt.call("search", {"continuation": page.continuation}),
                  page)
    return True

# SPDX-License-Identifier: Apache-2.0
"""
Dislike counts, which YouTube no longer shows: the estimate of Return
YouTube Dislike (returnyoutubedislikeapi.com), a public service that adds
the dislikes counted before YouTube hid them to what its users' browsers
report since. Asked only when the dislikes setting is on: the service
then learns which videos are opened.
"""

import json
import os
import threading

from vts import http
from vts.rules import valid_video_id

API_URL = "https://returnyoutubedislikeapi.com/votes?videoId={}"

_known = {}  # video id: dislikes, for this run
_lock = threading.Lock()


def api_url(ident):
    test = os.environ.get("VTS_RYD_URL")
    return (test + "/votes?videoId={}" if test else API_URL).format(ident)


def get(ident):
    """the estimated dislikes of the video @ident (an int), or None when the
    service doesn't say"""
    if not valid_video_id(ident):
        return None
    with _lock:
        if ident in _known:
            return _known[ident]
    r = http.fetch(http.Request(api_url(ident), max_size=64 * 1024,
                                timeout=8.0))
    n = None
    if r.status == 200:
        try:
            got = json.loads(r.body)
            d = got.get("dislikes") if isinstance(got, dict) else None
            if isinstance(d, int) and not isinstance(d, bool) and d >= 0:
                n = d
        except ValueError:
            pass
    with _lock:
        _known[ident] = n
    return n


def short(n):
    """12345 as 12K, as YouTube writes likes"""
    if n < 1000:
        return str(n)
    for div, unit in ((10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "K")):
        if n >= div:
            v = n / div
            s = f"{v:.1f}" if v < 10 else f"{v:.0f}"
            return s.removesuffix(".0") + unit
    return str(n)

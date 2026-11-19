# SPDX-License-Identifier: Apache-2.0
"""Small helpers: numbers, yes/no, dates."""

import time


def parse_long(s, lo, hi):
    """@s as an integer from @lo to @hi, or None"""
    if not s or not (s[0] == "-" or s[0].isdigit()):
        return None
    try:
        v = int(s, 10)
    except ValueError:
        return None
    if not s.lstrip("-").isascii() or v < lo or v > hi:
        return None
    return v


_YES = ("yes", "true", "on", "1")
_NO = ("no", "false", "off", "0")


def parse_bool(s):
    """True, False, or None for anything else"""
    s = s.lower()
    if s in _YES:
        return True
    if s in _NO:
        return False
    return None


def days_from_civil(y, m, d):
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _digits(s):
    return int(s) if s.isascii() and s.isdigit() else -1


def iso_time(iso):
    """seconds since 1970 of "2025-11-30T14:00:00+00:00", or None"""
    if (len(iso) < 19 or iso[4] != "-" or iso[7] != "-" or
            iso[10] not in "T " or iso[13] != ":" or iso[16] != ":"):
        return None
    y, mo, d = _digits(iso[0:4]), _digits(iso[5:7]), _digits(iso[8:10])
    h, mi, sec = _digits(iso[11:13]), _digits(iso[14:16]), _digits(iso[17:19])
    if (y < 1970 or not 1 <= mo <= 12 or not 1 <= d <= 31 or
            not 0 <= h <= 23 or not 0 <= mi <= 59 or not 0 <= sec <= 60):
        return None
    rest = iso[19:]
    if rest.startswith("."):
        rest = rest[1:]
        while rest and rest[0].isdigit():
            rest = rest[1:]
    zone = 0
    if rest[:1] in ("+", "-") and len(rest) >= 6 and rest[3] == ":":
        zh, zm = _digits(rest[1:3]), _digits(rest[4:6])
        if zh < 0 or zm < 0:
            return None
        zone = (zh * 3600 + zm * 60) * (-1 if rest[0] == "-" else 1)
    elif rest and rest != "Z":
        return None
    return days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60 + sec - zone


_UNITS = ((365 * 86400, "year"), (30 * 86400, "month"), (7 * 86400, "week"),
          (86400, "day"), (3600, "hour"), (60, "minute"))


def ago(iso, now=None):
    """ "3 days ago" for an ISO date, or None if it isn't one"""
    t = iso_time(iso)
    if t is None:
        return None
    if now is None:
        now = int(time.time())
    diff = now - t
    if diff < 60:
        return "just now"
    for secs, unit in _UNITS:
        n = diff // secs
        if n >= 1:
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return None


def format_time(s):
    """ "1:02:03" or "2:03" """
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def parse_duration(s):
    """seconds of "1:02:03" or "2:03", or None"""
    parts = s.split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isascii() and p.isdigit()
                                           for p in parts):
        return None
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def human_size(n):
    """ "2.1 MB" """
    for unit in ("B", "kB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"

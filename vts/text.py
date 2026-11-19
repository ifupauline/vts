# SPDX-License-Identifier: Apache-2.0
"""
Everything vts shows comes from strangers: titles, descriptions,
comments, channel names. A terminal obeys the escape sequences hidden in
text (colours, cursor moves, window titles, and in some terminals worse),
so all of it goes through clean() before display.
"""

import unicodedata

ELLIPSIS = "…"  # one column
REPLACEMENT = "\ufffd"

# the marks that reorder text on screen, used to disguise names or links
_BIDI = {0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F),
         *range(0x2066, 0x206A)}
_LINE_BREAKS = {"\n", "\u2028", "\u2029"}


def _table(multiline):
    t = {}
    for cp in (*range(0x20), *range(0x7F, 0xA0), *_BIDI):
        t[cp] = None
    t[ord("\t")] = " "
    for c in _LINE_BREAKS:
        t[ord(c)] = "\n" if multiline else " "
    return t


_TABLES = (_table(False), _table(True))


def clean(s, multiline=False):
    """
    @s safe for a terminal: tabs become a space, control characters (C0,
    DEL, C1, so every escape sequence) and the bidirectional overrides
    that can disguise text are removed, lone surrogates become U+FFFD.
    Line breaks are kept with @multiline, spaces otherwise.
    """
    if isinstance(s, bytes):
        s = s.decode("utf-8", "replace")
    s = s.translate(_TABLES[1 if multiline else 0])
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        s = "".join(REPLACEMENT if 0xD800 <= ord(c) <= 0xDFFF else c
                    for c in s)
    return s


def char_width(c):
    """columns taken by @c in a terminal: 0 (combining), 1 or 2 (wide)"""
    cp = ord(c)
    if cp < 0x7F:
        return 1 if cp >= 0x20 else 0
    if cp < 0xA0:
        return 0
    if cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
        return 0
    cat = unicodedata.category(c)
    if cat in ("Mn", "Me", "Cf"):
        return 0
    if 0x1160 <= cp <= 0x11FF:  # Hangul medial vowels and final consonants
        return 0
    if unicodedata.east_asian_width(c) in ("W", "F"):
        return 2
    return 1


def width(s):
    """columns taken by the cleaned string @s"""
    if s.isascii():
        return len(s)
    return sum(char_width(c) for c in s)


def fit(s, cols):
    """@s cut to at most @cols columns, with a "…" when it doesn't fit"""
    if cols <= 0:
        return ""
    if s.isascii() and len(s) <= cols:
        return s
    w = 0
    cut = 0
    for i, c in enumerate(s):
        cw = char_width(c)
        if w + cw > cols:
            return s[:cut] + ELLIPSIS
        w += cw
        if w <= cols - 1:
            cut = i + 1
    return s


def pad(s, cols):
    """@s fitted to @cols columns and padded with spaces to them"""
    s = fit(s, cols)
    return s + " " * max(0, cols - width(s))

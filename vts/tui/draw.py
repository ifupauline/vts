# SPDX-License-Identifier: Apache-2.0
"""
Drawing with curses: text cut to a width, wrapped text, colours.

Windows' curses (PDCurses) gives every character one cell, but a Korean,
Chinese or Japanese letter takes two on the screen (and a combining mark
none): what follows it lands in the wrong place. There, a line with such
characters is drawn by curses as blanks of its real width, and the text
itself is written into them after each refresh, the terminal placing the
letters.
"""

import curses

from vts import platform
from vts import text as T

C_ACCENT, C_DIM, C_BAR, C_ERROR, C_SELECT, C_LIVE = range(1, 7)

# each site's colours: headings (accent), second lines (dim), the top bar's
# background; in a terminal of 256 colours, then of 8 or 16
THEMES = {
    "youtube": {256: (160, 37, 160), 8: ("RED", "CYAN", "RED")},
    # Twitch's purple
    "twitch": {256: (135, 183, 92), 8: ("MAGENTA", "MAGENTA", "MAGENTA")},
    # the blue of Korea's flag, lighter for the second lines
    "soop": {256: (33, 117, 25), 8: ("BLUE", "CYAN", "BLUE")},
}
_theme = "youtube"
_bg = -1

scr = None  # the curses window, once the app has started

WIDE_BY_HAND = platform.WINDOWS
_ops = []  # since erase(): ("text", y, x, x2, s, attr) or ("cover", y, x, x2)
_shown = set()  # the pieces written by hand at the last refresh


def colours():
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    global _bg
    _bg = bg
    curses.init_pair(C_ERROR, curses.COLOR_RED, bg)
    curses.init_pair(C_SELECT, -1, bg)
    # a stream on now: red on every site, as on the websites
    curses.init_pair(C_LIVE, curses.COLOR_WHITE, curses.COLOR_RED)
    theme(_theme)


def theme(site):
    """the colours of @site (youtube, twitch, soop) from now on"""
    global _theme
    _theme = site if site in THEMES else "youtube"
    if not curses.has_colors():
        return
    t = THEMES[_theme]
    if curses.COLORS >= 256:
        accent, dim, bar = t[256]
        # YouTube's as they always were, in every terminal
        if _theme == "youtube":
            accent, dim, bar = t[8]
    else:
        accent, dim, bar = t[8]
    if isinstance(accent, str):
        accent, dim, bar = (getattr(curses, f"COLOR_{c}")
                            for c in (accent, dim, bar))
    curses.init_pair(C_ACCENT, accent, _bg)
    curses.init_pair(C_DIM, dim if curses.COLORS >= 8 else -1, _bg)
    curses.init_pair(C_BAR, curses.COLOR_WHITE, bar)


def pair(n):
    return curses.color_pair(n) if curses.has_colors() else 0


def lines():
    return curses.LINES


def cols():
    return curses.COLS


def _by_hand(s):
    return WIDE_BY_HAND and not s.isascii() and any(
        T.char_width(c) != 1 for c in s)


def put(y, x, s, attr=0):
    """@s at @y, @x as it is (curses refuses the very last cell)"""
    if _by_hand(s):
        # straight to the terminal: never a control character
        s = "".join("?" if ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 else c
                    for c in s)
        w = T.width(s)
        try:
            scr.addstr(y, x, " " * w, attr)
        except curses.error:
            pass
        _ops.append(("text", y, x, x + w, s, attr))
        return
    if WIDE_BY_HAND and _ops:
        _ops.append(("cover", y, x, x + len(s)))
    try:
        scr.addstr(y, x, s, attr)
    except curses.error:
        pass


def erase():
    """the whole screen blank, before drawing it again"""
    _ops.clear()
    scr.erase()


def _pieces():
    """what to write by hand: each text, less what was drawn over it"""
    out = []
    for i, op in enumerate(_ops):
        if op[0] != "text":
            continue
        _, y, x, _, s, attr = op
        covers = [(o[2], o[3]) for o in _ops[i + 1:] if o[1] == y]
        col, run, start = x, [], x
        for c in s:
            cw = T.char_width(c)
            hidden = any(a < col + max(cw, 1) and col < b for a, b in covers)
            if hidden or (not run and cw == 0):
                if run:
                    out.append((y, start, "".join(run), attr))
                run = []
                start = col + cw
            else:
                if not run:
                    start = col
                run.append(c)
            col += cw
        if run:
            out.append((y, start, "".join(run), attr))
    return out


_ANSI = None


def _sgr(attr):
    """the escape sequence of a curses attribute: colours, bold, reverse"""
    global _ANSI
    if _ANSI is None:
        names = ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA",
                 "CYAN", "WHITE")
        _ANSI = {getattr(curses, f"COLOR_{n}"): i for i, n in
                 enumerate(names)}
    codes = ["0"]
    for flag, code in ((curses.A_BOLD, "1"), (curses.A_DIM, "2"),
                       (curses.A_UNDERLINE, "4"), (curses.A_REVERSE, "7")):
        if attr & flag:
            codes.append(code)
    n = curses.pair_number(attr & curses.A_COLOR)
    if n and curses.has_colors():
        try:
            fg, bg = curses.pair_content(n)
        except curses.error:
            fg = bg = -1
        for c, base in ((fg, 30), (bg, 40)):
            if c >= 16:
                codes.append(f"{base + 8};5;{c}")
            elif c in _ANSI:
                codes.append(str(base + _ANSI[c]))
            elif 8 <= c < 16 and c - 8 in _ANSI:
                codes.append(str(base + 60 + _ANSI[c - 8]))
    return f"\x1b[{';'.join(codes)}m"


def refresh():
    """curses' refresh, and on Windows the lines it can't place"""
    global _shown
    if not WIDE_BY_HAND:
        scr.refresh()
        return
    new = set(_pieces())
    # gone since last time: blank (as curses believes they are) first
    gone = "".join(f"\x1b[{y + 1};{x + 1}H{_sgr(a)}{' ' * T.width(s)}"
                   for y, x, s, a in _shown - new)
    if gone:
        platform.term_write(f"\x1b7{gone}\x1b[0m\x1b8")
    scr.refresh()
    if new:
        platform.term_write("\x1b7" + "".join(
            f"\x1b[{y + 1};{x + 1}H{_sgr(a)}{s}" for y, x, s, a in new) +
            "\x1b[0m\x1b8")
    _shown = new


def text(y, x, s, width, attr=0):
    """@s cut to @width columns at line @y, column @x"""
    if width <= 0 or y < 0 or y >= curses.LINES:
        return
    put(y, x, T.fit(s or "", width), attr)


def hline(y, x, width, attr=0):
    put(y, x, " " * max(0, width), attr)


def clear_line(y):
    if WIDE_BY_HAND and _ops:
        _ops.append(("cover", y, 0, 1 << 16))
    try:
        scr.move(y, 0)
        scr.clrtoeol()
    except curses.error:
        pass


def _wrap_at(s, width):
    """how many characters of @s (one line) fit in @width, cut at a space"""
    w = 0
    space = 0
    for i, c in enumerate(s):
        cw = T.char_width(c)
        if w + cw > width:
            return space or i or 1
        if c == " ":
            space = i + 1
        w += cw
    return len(s)


def wrap(s, width, max_lines):
    """the lines of @s wrapped at @width, at most @max_lines"""
    out = []
    if width <= 0:
        return out
    for line in (s or "").split("\n"):
        if len(out) >= max_lines:
            break
        if not line:
            out.append("")
            continue
        done = 0
        while done < len(line) and len(out) < max_lines:
            n = _wrap_at(line[done:], width)
            out.append(line[done:done + n])
            done += n
    if s and s.endswith("\n") and out and out[-1] == "":
        out.pop()
    return out


def wrapped(y, x, s, width, max_lines, attr=0):
    """@s wrapped at @width from line @y, at most @max_lines; lines used"""
    ls = wrap(s, width, max_lines)
    for i, line in enumerate(ls):
        text(y + i, x, line, width, attr)
    return len(ls)


def wrapped_lines(s, width, max_lines):
    return len(wrap(s, width, max_lines))

# SPDX-License-Identifier: Apache-2.0
"""Drawing with curses: text cut to a width, wrapped text, colours."""

import curses

from vts import text as T

C_ACCENT, C_DIM, C_BAR, C_ERROR, C_SELECT = range(1, 6)

scr = None  # the curses window, once the app has started


def colours():
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(C_ACCENT, curses.COLOR_RED, bg)
    curses.init_pair(C_DIM, curses.COLOR_CYAN if curses.COLORS >= 8 else -1,
                     bg)
    curses.init_pair(C_BAR, curses.COLOR_WHITE, curses.COLOR_RED)
    curses.init_pair(C_ERROR, curses.COLOR_RED, bg)
    curses.init_pair(C_SELECT, -1, bg)


def pair(n):
    return curses.color_pair(n) if curses.has_colors() else 0


def lines():
    return curses.LINES


def cols():
    return curses.COLS


def put(y, x, s, attr=0):
    """@s at @y, @x as it is (curses refuses the very last cell)"""
    try:
        scr.addstr(y, x, s, attr)
    except curses.error:
        pass


def text(y, x, s, width, attr=0):
    """@s cut to @width columns at line @y, column @x"""
    if width <= 0 or y < 0 or y >= curses.LINES:
        return
    put(y, x, T.fit(s or "", width), attr)


def hline(y, x, width, attr=0):
    put(y, x, " " * max(0, width), attr)


def clear_line(y):
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

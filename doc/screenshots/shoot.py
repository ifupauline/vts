#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Make the screenshots of the README: runs vts on a pseudo terminal against
the test server (the recorded YouTube answers, a made-up thumbnail), reads
its screen with pyte and writes each one as an SVG picture.

usage: shoot.py VTS [OUTDIR]   (OUTDIR: this folder)
"""
import os
import pty
import fcntl
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from xml.sax.saxutils import escape

import pyte

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.join(HERE, "..", "..", "tests")
COLS, ROWS = 120, 32
CELL_W, CELL_H, FONT = 8.4, 17, 14

# a dark theme for the 16 colours, then the terminal's own
PALETTE = {
    "black": "#1d1f21", "red": "#e2504c", "green": "#98c379",
    "brown": "#e5c07b", "yellow": "#e5c07b", "blue": "#61afef",
    "magenta": "#c678dd", "cyan": "#7ec7d6", "white": "#d0d0d0",
}
FG, BG = "#d0d0d0", "#16181c"


def colour(name, default):
    if name == "default":
        return default
    if name in PALETTE:
        return PALETTE[name]
    if len(name) == 6:
        return "#" + name
    return default


def svg(screen, title):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'width="{COLS * CELL_W + 24:.0f}" height="{ROWS * CELL_H + 52}" '
           f'font-family="DejaVu Sans Mono, Menlo, Consolas, monospace" '
           f'font-size="{FONT}">',
           f'<title>{escape(title)}</title>',
           f'<rect width="100%" height="100%" rx="8" fill="{BG}"/>',
           '<circle cx="18" cy="16" r="5" fill="#ed6a5e"/>'
           '<circle cx="34" cy="16" r="5" fill="#f5bf4f"/>'
           '<circle cx="50" cy="16" r="5" fill="#61c554"/>',
           '<g transform="translate(12,32)">']
    for y in range(ROWS):
        line = screen.buffer[y]
        x = 0
        while x < COLS:
            ch = line[x]
            fg, bg = colour(ch.fg, FG), colour(ch.bg, BG)
            if ch.reverse:
                fg, bg = (bg if ch.bg != "default" else BG,
                          fg if ch.fg != "default" else FG)
            # a run of cells with the same look
            run = x + 1
            while run < COLS and (line[run].fg, line[run].bg,
                                  line[run].bold, line[run].reverse) == \
                    (ch.fg, ch.bg, ch.bold, ch.reverse):
                run += 1
            if bg != BG:
                out.append(f'<rect x="{x * CELL_W:.1f}" y="{y * CELL_H}" '
                           f'width="{(run - x) * CELL_W + 0.6:.1f}" '
                           f'height="{CELL_H + 0.5}" fill="{bg}"/>')
            for i in range(x, run):
                c = line[i].data
                if not c or c == " ":
                    continue
                if 0x2580 <= ord(c[0]) <= 0x259f:
                    # block characters: drawn, fonts differ too much
                    out.append(block(c, i, y, fg))
                    continue
                weight = ' font-weight="bold"' if ch.bold else ""
                out.append(f'<text x="{i * CELL_W:.1f}" '
                           f'y="{y * CELL_H + 13}" fill="{fg}"{weight}>'
                           f'{escape(c)}</text>')
            x = run
    out.append("</g></svg>")
    return "\n".join(out) + "\n"


def block(c, x, y, fg):
    """the block elements as rectangles of the cell"""
    cp = ord(c)
    x0, y0 = x * CELL_W, y * CELL_H
    w, h = CELL_W + 0.6, CELL_H + 0.5
    if cp == 0x2580:                       # upper half
        r = [(0, 0, 1, 0.5)]
    elif 0x2581 <= cp <= 0x2588:           # lower eighths
        f = (cp - 0x2580) / 8
        r = [(0, 1 - f, 1, f)]
    elif 0x2589 <= cp <= 0x258f:           # left eighths
        f = (0x2590 - cp) / 8
        r = [(0, 0, f, 1)]
    elif cp == 0x2590:
        r = [(0.5, 0, 0.5, 1)]
    elif cp == 0x2594:
        r = [(0, 0, 1, 0.125)]
    elif cp == 0x2595:
        r = [(0.875, 0, 0.125, 1)]
    else:                                  # quadrants
        q = {0x2596: "3", 0x2597: "4", 0x2598: "1", 0x2599: "134",
             0x259a: "14", 0x259b: "123", 0x259c: "124", 0x259d: "2",
             0x259e: "23", 0x259f: "234"}.get(cp, "1234")
        pos = {"1": (0, 0), "2": (0.5, 0), "3": (0, 0.5), "4": (0.5, 0.5)}
        r = [(*pos[k], 0.5, 0.5) for k in q]
    return "".join(f'<rect x="{x0 + a * w:.1f}" y="{y0 + b * h:.1f}" '
                   f'width="{c_ * w:.1f}" height="{d * h:.1f}" fill="{fg}"/>'
                   for a, b, c_, d in r)


class Term:
    def __init__(self, vts, env):
        sys.path.insert(0, TESTS)
        from test_cli import teach_pyte
        teach_pyte(pyte)
        self.screen = pyte.Screen(COLS, ROWS)
        self.stream = pyte.ByteStream(self.screen)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(vts, [vts], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))

    def text(self):
        return "\n".join(self.screen.display)

    def pump(self, until=None, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.stream.feed(os.read(self.fd, 1 << 16))
                except OSError:
                    return
            if until and until in self.text():
                # let the rest of the screen arrive
                self.pump(None, 0.6)
                return
        if until:
            sys.exit(f"never shown: {until!r}\n{self.text()}")

    def keys(self, k, until=None):
        os.write(self.fd, k.encode())
        self.pump(until)


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__.strip())
    vts = os.path.abspath(sys.argv[1])
    outdir = sys.argv[2] if len(sys.argv) == 3 else HERE
    work = tempfile.mkdtemp(prefix="vts-shots-")
    port = os.path.join(work, "port")
    server = subprocess.Popen([sys.executable,
                               os.path.join(TESTS, "fake_server.py"), port,
                               os.path.join(TESTS, "fixtures", "youtube")])
    while not os.path.exists(port):
        time.sleep(0.05)
    url = "http://127.0.0.1:" + open(port).read().strip()
    env = {"PATH": os.environ["PATH"], "HOME": work, "LANG": "en_US.UTF-8",
           "TERM": "xterm-256color", "COLORTERM": "truecolor",
           "XDG_CONFIG_HOME": os.path.join(work, ".config"),
           "XDG_DATA_HOME": os.path.join(work, ".local", "share"),
           "XDG_CACHE_HOME": os.path.join(work, ".cache"),
           "XDG_RUNTIME_DIR": os.path.join(work, "run"),
           "VTS_BASE_URL": url, "VTS_IMAGE_URL": url}
    # a favourite, so Home has something
    subprocess.run([vts, "fav", "add", "-g", "Science", "@kurzgesagt"],
                   env=env, check=True, capture_output=True)
    subprocess.run([vts, "check", "-q", "-n"], env=env,
                   capture_output=True)

    shots = []
    t = Term(vts, env)
    try:
        t.pump("Kurzgesagt")
        shots.append(("home", "vts: Home", svg(t.screen, "Home")))
        t.keys("/", None)
        t.keys("linus tor", "linus torvalds interview")
        shots.append(("suggestions", "vts: search suggestions",
                      svg(t.screen, "Suggestions")))
        t.keys("\x15linus torvalds\r", "53:44")
        t.pump(None, 2)
        shots.append(("search", "vts: search results",
                      svg(t.screen, "Search results")))
        t.keys("\r", "Up next")
        t.pump(None, 2)
        shots.append(("video", "vts: a video page",
                      svg(t.screen, "A video page")))
        t.keys("4", "Saved in")
        t.keys("jj", "highest quality")
        t.pump(None, 1)
        shots.append(("settings", "vts: the settings",
                      svg(t.screen, "Settings")))
        t.keys("1q")
        os.waitpid(t.pid, 0)
    finally:
        server.terminate()
    for name, title, text in shots:
        with open(os.path.join(outdir, name + ".svg"), "w") as f:
            f.write(text)
        print(f"{name}.svg  {title}")


if __name__ == "__main__":
    main()

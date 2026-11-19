# SPDX-License-Identifier: Apache-2.0
"""
Thumbnails for the full screen app. They are downloaded from i.ytimg.com
while the app goes on (nothing waits for them), kept in
~/.cache/vts/thumbs for 30 days, and turned into what the terminal can
show: kitty or iTerm2 images, sixels, or coloured block characters
anywhere else. Only video identifiers leave vts: the addresses are built
here, never taken from YouTube's answers.

Pictures are decoded with Pillow; the blocks come from chafa when it is
installed (they look better), from Pillow's own half blocks otherwise.
"""

import base64
import concurrent.futures
import io
import os
import subprocess
import threading

from vts import http, log, platform
from vts.rules import valid_video_id

DEFAULT_IMAGE_BASE = "https://i.ytimg.com"
MAX_FILE = 512 * 1024  # a thumbnail is about 20 kB
MAX_SIDE = 2048  # pixels
MAX_DOWNLOADS = 6  # at once
MAX_RENDERED = 24  # kept ready to draw
KEEP_DAYS = 30
MODES = ("blocks", "sixel", "kitty", "iterm2")

KITTY_DELETE = "\x1b_Ga=d\x1b\\"


class Unavailable(Exception):
    pass


class Thumb:
    __slots__ = ("text", "cols", "rows", "lines")

    def __init__(self, text, cols, rows, lines):
        self.text = text  # for the terminal, as is
        self.cols = cols  # the cells it covers
        self.rows = rows
        self.lines = lines  # blocks: one line of text per row


def thumb_id(it):
    """the video whose thumbnail stands for @it (a video, Short, playlist)"""
    if it.kind in ("video", "short"):
        return it["id"] if valid_video_id(it["id"]) else None
    if it.kind == "playlist":
        # the picture of a playlist is that of one of its videos
        prefix = "https://i.ytimg.com/vi/"
        url = it["thumbnail"] or ""
        if url.startswith(prefix) and url[len(prefix) + 11:][:1] == "/":
            ident = url[len(prefix):len(prefix) + 11]
            return ident if valid_video_id(ident) else None
    return None


def detect():
    """what the terminal shows best, from its name and environment"""
    env = os.environ
    term = env.get("TERM", "")
    prog = env.get("TERM_PROGRAM", "")
    if (prog == "ghostty" or term in ("xterm-ghostty", "xterm-kitty") or
            env.get("KITTY_WINDOW_ID") or env.get("KONSOLE_VERSION") or
            prog == "WezTerm"):
        return "kitty"
    if prog == "iTerm.app":
        return "iterm2"
    if term.startswith(("foot", "mlterm", "contour")) or prog in (
            "mintty",) or env.get("WT_SESSION"):
        return "sixel"
    return "blocks"


def _windows_vt():
    """have the Windows console obey escape sequences: whether it does"""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        out = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(out, ctypes.byref(mode)):
            return False
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(mode.value & 4 or k32.SetConsoleMode(out,
                                                         mode.value | 4))
    except (AttributeError, OSError):
        return False


def colours():
    """the colours the terminal takes: 'full', '256' or '16'"""
    # Windows' consoles take them all (since Windows 10), and Windows
    # Terminal doesn't always say it is there (WT_SESSION)
    if os.environ.get("COLORTERM") in ("truecolor", "24bit") or \
            os.environ.get("WT_SESSION") or platform.WINDOWS:
        return "full"
    if "256" in os.environ.get("TERM", ""):
        return "256"
    return "16"


def cell_size():
    """(width, height) of a character cell in pixels, a guess if unknown"""
    if not platform.WINDOWS:
        try:
            import fcntl
            import struct
            import termios
            import sys
            ws = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ,
                             b"\0" * 8)
            rows, cols, xp, yp = struct.unpack("HHHH", ws)
            if cols and rows and xp and yp:
                w, h = xp // cols, yp // rows
                if w >= 2 and h >= 2:
                    return w, h
        except (OSError, ImportError, ValueError):
            pass
    # the usual shape: twice as high as wide
    return 10, 20


def fit(w, h, cols, rows, cw, ch):
    """the cells a @w x @h picture takes in @cols x @rows, keeping its shape"""
    ratio = (w / h) * (ch / cw)  # columns per row
    c = cols
    r = round(c / ratio)
    if r > rows:
        r = rows
        c = round(r * ratio)
    return max(1, min(cols, c)), max(1, min(rows, r))


# --- the pictures, as text ---------------------------------------------------

_SGR16 = [(0, 0, 0), (205, 0, 0), (0, 205, 0), (205, 205, 0), (0, 0, 238),
          (205, 0, 205), (0, 205, 205), (229, 229, 229), (127, 127, 127),
          (255, 0, 0), (0, 255, 0), (255, 255, 0), (92, 92, 255),
          (255, 0, 255), (0, 255, 255), (255, 255, 255)]


def _c256(r, g, b):
    def q(v):
        return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40
    return 16 + 36 * q(r) + 6 * q(g) + q(b)


def _c16(r, g, b):
    best = min(range(16), key=lambda i: (_SGR16[i][0] - r) ** 2 +
               (_SGR16[i][1] - g) ** 2 + (_SGR16[i][2] - b) ** 2)
    return best


def _sgr(fg, bg, depth):
    if depth == "full":
        return f"\x1b[38;2;{fg[0]};{fg[1]};{fg[2]};48;2;{bg[0]};{bg[1]};" \
               f"{bg[2]}m"
    if depth == "256":
        return f"\x1b[38;5;{_c256(*fg)};48;5;{_c256(*bg)}m"
    f, b = _c16(*fg), _c16(*bg)
    return f"\x1b[{30 + f if f < 8 else 82 + f};{40 + b if b < 8 else 92 + b}m"


def blocks_pillow(img, cols, rows, depth):
    """half blocks: the top pixel in front, the one under it behind"""
    img = img.convert("RGB").resize((cols, rows * 2))
    px = img.load()
    out = []
    for y in range(rows):
        line = []
        last = None
        for x in range(cols):
            fg, bg = px[x, 2 * y], px[x, 2 * y + 1]
            s = _sgr(fg, bg, depth)
            if s != last:
                line.append(s)
                last = s
            line.append("▀")
        line.append("\x1b[0m")
        out.append("".join(line))
    return "\n".join(out)


def kitty(img, cols, rows, cw, ch):
    """kitty's own images: a PNG in pieces of 4096 bytes"""
    img = img.convert("RGB").resize((cols * cw, rows * ch))
    b = io.BytesIO()
    img.save(b, "PNG")
    data = base64.b64encode(b.getvalue()).decode()
    parts = [data[i:i + 4096] for i in range(0, len(data), 4096)]
    out = []
    for i, p in enumerate(parts):
        more = 1 if i < len(parts) - 1 else 0
        if i == 0:
            out.append(f"\x1b_Ga=T,f=100,q=2,c={cols},r={rows},m={more};"
                       f"{p}\x1b\\")
        else:
            out.append(f"\x1b_Gm={more};{p}\x1b\\")
    return "".join(out)


def iterm2(jpeg, cols, rows):
    """iTerm2's images: the file as it is"""
    return (f"\x1b]1337;File=inline=1;size={len(jpeg)};width={cols};"
            f"height={rows};preserveAspectRatio=1:"
            f"{base64.b64encode(jpeg).decode()}\a")


def sixel(img, cols, rows, cw, ch):
    """sixels: six rows of pixels a line, 255 colours"""
    w, h = cols * cw, rows * ch
    img = img.convert("RGB").resize((w, h)).quantize(255)
    pal = img.getpalette()[:255 * 3]
    px = img.load()
    out = ["\x1bPq", f'"1;1;{w};{h}']
    ncol = len(pal) // 3
    for i in range(ncol):
        r, g, b = pal[3 * i:3 * i + 3]
        out.append(f"#{i};2;{r * 100 // 255};{g * 100 // 255};"
                   f"{b * 100 // 255}")
    for band in range(0, h, 6):
        used = {}
        for x in range(w):
            for dy in range(6):
                y = band + dy
                if y < h:
                    used.setdefault(px[x, y], None)
        first = True
        for c in used:
            if not first:
                out.append("$")
            first = False
            out.append(f"#{c}")
            run_ch, run_n = None, 0
            row = []
            for x in range(w):
                bits = 0
                for dy in range(6):
                    y = band + dy
                    if y < h and px[x, y] == c:
                        bits |= 1 << dy
                chr_ = chr(63 + bits)
                if chr_ == run_ch:
                    run_n += 1
                else:
                    if run_ch:
                        row.append(f"!{run_n}{run_ch}" if run_n > 3
                                   else run_ch * run_n)
                    run_ch, run_n = chr_, 1
            if run_ch:
                row.append(f"!{run_n}{run_ch}" if run_n > 3
                           else run_ch * run_n)
            out.append("".join(row))
        out.append("-")
    out.append("\x1b\\")
    return "".join(out)


def blocks_chafa(chafa, path, cols, rows, depth):
    try:
        p = subprocess.run(
            [chafa, "--format=symbols", f"--size={cols}x{rows}",
             "--stretch", "--symbols=block+border+space-wide",
             f"--colors={depth}", "--animate=off", "--polite=on", path],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
            **platform._popen_flags(False))
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode:
        return None
    text = p.stdout.decode("utf-8", "replace").rstrip("\n")
    # only colours and characters: what ncurses wouldn't expect goes
    return text.replace("\x1b[?25l", "").replace("\x1b[?25h", "")


class Thumbs:
    def __init__(self, mode="auto"):
        """@mode: a setting's value; Unavailable when nothing can show"""
        if mode == "no":
            raise Unavailable("off in the settings")
        try:
            import PIL.Image  # noqa: F401
        except ImportError:
            raise Unavailable("Pillow is missing (pip install pillow)") \
                from None
        if platform.WINDOWS and not _windows_vt():
            raise Unavailable("this console doesn't take escape sequences")
        self.mode = detect() if mode == "auto" else mode
        self.depth = colours()
        self.chafa = platform.find_program("chafa")
        self.tmux = bool(os.environ.get("TMUX"))
        base = os.environ.get("VTS_IMAGE_URL", "")
        self.base = base if base.startswith(("http://", "https://")) \
            else DEFAULT_IMAGE_BASE
        d = platform.cache_dir()
        if not d:
            raise Unavailable("no folder for them")
        self.dir = os.path.join(d, "thumbs")
        try:
            platform.mkdirs(self.dir)
        except OSError as e:
            raise Unavailable(f"{self.dir}: {e.strerror}") from None
        platform.prune_dir(self.dir, KEEP_DAYS * 86400)
        self._pool = concurrent.futures.ThreadPoolExecutor(MAX_DOWNLOADS)
        self._lock = threading.Lock()
        self._pending = {}
        self._done = []
        self._failed = set()
        self._rendered = {}  # (id, cols, rows, cell) -> Thumb
        self._order = []

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def busy(self):
        return bool(self._pending)

    def _path(self, ident):
        return os.path.join(self.dir, f"{ident}.jpg")

    def _download(self, ident):
        r = http.fetch(http.Request(f"{self.base}/vi/{ident}/mqdefault.jpg",
                                    max_size=MAX_FILE))
        why = None
        if r.error:
            why = r.message
        elif r.status != 200:
            why = "not found"
        elif not r.body.startswith(b"\xff\xd8") or len(r.body) <= 4:
            why = "not a JPEG image"
        if not why:
            try:
                platform.write_atomic(self._path(ident), r.body)
            except OSError as e:
                why = e.strerror
        with self._lock:
            self._done.append((ident, why))

    def poll(self):
        """take in the downloads that ended: how many"""
        with self._lock:
            done, self._done = self._done, []
        for ident, why in done:
            self._pending.pop(ident, None)
            if why:
                log.dbg(f"thumbnail {ident}: {why}")
                self._failed.add(ident)
        return len(done)

    def get(self, ident, cols, rows):
        """
        the thumbnail of @ident fitted in @cols x @rows cells: a Thumb,
        None while it is being downloaded, False if there is none
        """
        if not valid_video_id(ident) or cols < 1 or rows < 1:
            return False
        cw, ch = cell_size()
        key = (ident, cols, rows, cw, ch)
        t = self._rendered.get(key)
        if t:
            return t
        if ident in self._failed:
            return False
        if ident in self._pending:
            return None
        path = self._path(ident)
        try:
            data = platform.read_file(path, MAX_FILE, binary=True)
        except FileNotFoundError:
            if len(self._pending) < MAX_DOWNLOADS:
                self._pending[ident] = self._pool.submit(self._download,
                                                         ident)
            return None
        except (OSError, ValueError):
            self._failed.add(ident)
            return False
        t = self._render(data, path, cols, rows, cw, ch)
        if not t:
            # broken: try once more from YouTube the next time
            try:
                os.unlink(path)
            except OSError:
                pass
            self._failed.add(ident)
            return False
        self._rendered[key] = t
        self._order.append(key)
        if len(self._order) > MAX_RENDERED:
            self._rendered.pop(self._order.pop(0), None)
        return t

    def _render(self, data, path, cols, rows, cw, ch):
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data))
            if img.format != "JPEG" or max(img.size) > MAX_SIDE:
                return None
            img.load()
        except (OSError, ValueError, Image.DecompressionBombError):
            return None
        c, r = fit(img.width, img.height, cols, rows, cw, ch)
        if self.mode == "kitty":
            text = kitty(img, c, r, cw, ch)
        elif self.mode == "iterm2":
            text = iterm2(data, c, r)
        elif self.mode == "sixel":
            text = sixel(img, c, r, cw, ch)
        else:
            text = None
            if self.chafa:
                text = blocks_chafa(self.chafa, path, c, r, self.depth)
            if not text:
                text = blocks_pillow(img, c, r, self.depth)
            return Thumb(text, c, r, True)
        if self.tmux:
            # through tmux to the terminal
            text = "\x1bPtmux;" + text.replace("\x1b", "\x1b\x1b") + "\x1b\\"
        return Thumb(text, c, r, False)

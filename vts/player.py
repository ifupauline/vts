# SPDX-License-Identifier: Apache-2.0
"""
Playing with mpv (which asks yt-dlp for the streams), and downloading
with yt-dlp. vts starts them as programs, never through a shell, with
links it builds itself from checked identifiers, and follows mpv through
its JSON IPC (a socket, a named pipe on Windows) to know where you are
in each video.
"""

import itertools
import math
import json
import os
import queue
import re
import subprocess
import threading
import time

from vts import log, platform
from vts.config import QUALITY_AUDIO, QUALITY_BEST, SOUND_QUALITIES
from vts.rules import Item, valid_video_id
from vts.text import clean

CONNECT_TIME = 20.0  # how long mpv may take to open its socket
QUIT_TIME = 3.0  # and to quit when asked

QUALITIES = (QUALITY_BEST, 2160, 1440, 1080, 720, 480, 360, 240, 144,
             QUALITY_AUDIO)
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
LOSSLESS = ("flac", "wav")
# what each sound quality takes from YouTube (the stream whose bitrate
# is closest to it, in kbit/s; None: the best there is) and the bitrate
# of a conversion
_SOUND = {"high": (None, "0"), "medium": (128, "128K"), "low": (64, "64K")}
_LANG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        "0123456789-,")


def quality_name(q):
    """ "720p (HD)", "Sound only" """
    return {QUALITY_BEST: "Best", QUALITY_AUDIO: "Sound only",
            2160: "2160p (4K)", 1440: "1440p (2K)", 1080: "1080p (Full HD)",
            720: "720p (HD)"}.get(q, f"{q}p")


def sound_name(sound):
    """ "Medium (about 128 kbit/s)" """
    return {"high": "High (the best YouTube has)",
            "medium": "Medium (about 128 kbit/s)",
            "low": "Low (about 64 kbit/s, half the data)"}[sound]


def play_name(quality, sound="high"):
    """ "720p (HD)", "Sound only, low quality" """
    if quality == QUALITY_AUDIO:
        return f"Sound only, {sound} quality"
    return quality_name(quality)


def ytdl_format(quality):
    """the yt-dlp format for @quality"""
    if quality == QUALITY_AUDIO:
        return "bestaudio/best"
    if quality == QUALITY_BEST:
        return "bestvideo+bestaudio/best"
    # and when nothing fits under that height, whatever there is
    return (f"bestvideo[height<=?{quality}]+bestaudio/"
            f"best[height<=?{quality}]/best")


def ytdl_sort(quality, sound):
    """
    how yt-dlp should rank the streams for the sound only in @sound, or
    None for its own order (by bitrate alone, it would put a 70 kbit/s
    Opus before a 128 kbit/s M4A)
    """
    rate = _SOUND[sound][0] if quality == QUALITY_AUDIO else None
    return f"abr~{rate}" if rate else None


def video_url(it):
    """the page of @it on YouTube, built from its checked id"""
    ident = it["id"] if isinstance(it, Item) else it
    if not valid_video_id(ident):
        raise ValueError("not a video")
    return f"https://www.youtube.com/watch?v={ident}"


def langs_ok(s):
    """ "fr,en": letters, digits, - and commas only"""
    return bool(s) and set(s) <= _LANG_CHARS


class PlayOptions:
    def __init__(self, cfg=None):
        self.quality = cfg.quality if cfg else 1080
        self.speed = 1.0
        self.subs = (cfg.subtitles or None) if cfg else None
        self.no_subs = False  # none at all, whatever mpv would pick
        self.terminal = False  # draw the video in the terminal
        self.resume = True  # start where the history says you stopped
        self.start = -1  # seconds into the first video, -1 for none
        self.ytdl = cfg.downloader if cfg else None
        # for the sound only: high, medium or low
        self.sound = cfg.sound_quality if cfg else "high"

    def copy(self):
        o = PlayOptions()
        o.__dict__.update(self.__dict__)
        return o


class PlayError(Exception):
    pass


class _Track:
    __slots__ = ("item", "start", "pos", "len", "played")

    def __init__(self, item, start):
        self.item = item
        self.start = start
        self.pos = 0.0
        self.len = 0.0
        self.played = False


_count = itertools.count()


class Player:
    """
    mpv playing @items (videos or Shorts, one after the other). With @lib,
    where each video starts comes from the history (o.resume) and where it
    was left goes back into it.
    """

    def __init__(self, player, lib, items, o):
        if not items:
            raise PlayError("nothing to play")
        self.lib = lib
        self.player = player
        self.opts = o
        self.tracks = []
        for i, it in enumerate(items):
            local = "file" in it.f
            if not local and not valid_video_id(it["id"]):
                raise PlayError("not a video")
            start = 0
            if i == 0 and o.start >= 0:
                start = o.start
            elif o.resume and lib and not local and "live" not in it.f:
                start = lib.hist_resume(it["id"])
            self.tracks.append(_Track(Item(it.kind, **it.f), start))
        self.cur = 0
        self.paused = False
        self.volume = None
        self.status = None
        self.conn = None
        self.addr = platform.ipc_address(f"mpv-{next(_count)}")
        if not self.addr:
            raise PlayError("no folder for mpv's socket")
        if not platform.find_program(player):
            raise PlayError(f"{player} is not installed")
        ytdl = None
        if o.ytdl:
            # mpv plays YouTube through yt-dlp: without it, nothing
            ytdl = platform.find_program(o.ytdl)
            if not ytdl:
                raise PlayError(f"{o.ytdl} is not installed: mpv needs it "
                                "to play YouTube videos")
            # mpv reads its script options split at commas
            if "," in ytdl:
                ytdl = None
        self.log_file = None
        d = platform.cache_dir()
        if d:
            try:
                platform.mkdirs(d)
                self.log_file = os.path.join(d, "mpv.log")
            except OSError:
                pass
        args = self._args(o, ytdl)
        if not platform.WINDOWS:
            try:
                os.unlink(self.addr)
            except OSError:
                pass
        try:
            self.proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL,
                stdout=None if o.terminal else subprocess.DEVNULL,
                stderr=None if o.terminal else subprocess.DEVNULL,
                **platform._popen_flags(True))
        except OSError:
            raise PlayError(f"can't start {player}") from None
        self.running = True
        self.started = time.monotonic()

    def _args(self, o, ytdl):
        a = [self.player, f"--input-ipc-server={self.addr}"]
        if self.log_file:
            a.append(f"--log-file={self.log_file}")
        # the yt-dlp vts knows about, not an older one mpv may find first
        if ytdl:
            a.append(f"--script-opts=ytdl_hook-ytdl_path={ytdl}")
        a.append(f"--ytdl-format={ytdl_format(o.quality)}")
        sort = ytdl_sort(o.quality, o.sound)
        if sort:
            a.append(f"--ytdl-raw-options-append=format-sort={sort}")
        if o.quality == QUALITY_AUDIO:
            a.append("--no-video")
        if o.speed > 0 and (o.speed < 0.999 or o.speed > 1.001):
            a.append(f"--speed={o.speed:.2f}")
        if o.no_subs:
            a.append("--sid=no")
        elif o.subs and langs_ok(o.subs):
            a += [f"--slang={o.subs}",
                  f"--ytdl-raw-options-append=sub-langs={o.subs}",
                  "--ytdl-raw-options-append=write-subs=",
                  "--ytdl-raw-options-append=write-auto-subs="]
        if o.terminal:
            a += ["--vo=tct", "--really-quiet"]
        elif o.quality != QUALITY_AUDIO:
            a.append("--force-window=immediate")
        # each video with its own start; links built here, never options
        for t in self.tracks:
            if t.start > 0:
                a += ["--{", f"--start={int(t.start)}"]
            if "file" in t.item.f:
                # a downloaded file: its whole path, never an option
                a.append(os.path.abspath(t.item.f["file"]))
            else:
                a.append(video_url(t.item))
            if t.start > 0:
                a.append("--}")
        return a

    # --- following it -----------------------------------------------------

    def current(self):
        """the video that plays (one of the items given)"""
        return self.tracks[self.cur].item if self.cur < len(self.tracks) \
            else None

    def now(self):
        """(index, position, length) of what plays, in seconds"""
        t = self.tracks[self.cur if self.cur < len(self.tracks) else 0]
        return self.cur, int(t.pos), int(t.len)

    def _send(self, obj):
        if not self.conn:
            return False
        try:
            self.conn.send(json.dumps(obj).encode() + b"\n")
            return True
        except OSError:
            return False

    def command(self, *words):
        """send mpv a command: "cycle", "pause"; False if it can't be"""
        return self._send({"command": list(words)})

    def append(self, it):
        """@it after the others: whether mpv took it"""
        if not valid_video_id(it["id"]):
            return False
        if not self.command("loadfile", video_url(it), "append"):
            return False
        self.tracks.append(_Track(Item(it.kind, **it.f), 0))
        return True

    def _connect(self):
        try:
            self.conn = platform.IpcConnection(self.addr)
        except OSError:
            self.conn = None
            return
        for n, name in enumerate(("playlist-pos", "time-pos", "duration",
                                  "media-title", "pause", "volume"), 1):
            if not self._send({"command": ["observe_property", n, name]}):
                log.dbg("player: can't follow mpv")

    def _on_line(self, line):
        try:
            ev = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(ev, dict) or ev.get("event") != "property-change":
            return
        name = ev.get("name")
        data = ev.get("data")
        number = isinstance(data, (int, float)) and not isinstance(data, bool)
        if name == "playlist-pos":
            if number and 0 <= data < len(self.tracks):
                self.cur = int(data)
            return
        if name == "pause":
            self.paused = data is True
            return
        if name == "volume":
            if number:
                self.volume = data
            return
        t = self.tracks[self.cur if self.cur < len(self.tracks) else 0]
        if name == "time-pos" and number:
            t.pos = float(data)
            t.played = True
        elif name == "duration" and number:
            t.len = float(data)
        elif (name == "media-title" and isinstance(data, str) and
              "title" not in t.item.f and not data.startswith("https://")):
            t.item.f["title"] = clean(data)

    def _read(self, timeout):
        try:
            lines = self.conn.read_lines(timeout)
        except (OSError, ConnectionError):
            # mpv closed its side: it is quitting
            self.conn.close()
            self.conn = None
            return
        for line in lines:
            self._on_line(line)

    def poll(self, timeout=0.0):
        """follow the player for up to @timeout seconds: whether it plays"""
        until = time.monotonic() + max(0.0, timeout)
        while self.running:
            left = until - time.monotonic()
            st = self.proc.poll()
            if st is not None:
                self.status = st
                # what is still in the socket
                if self.conn:
                    self._read(0)
                self.running = False
                break
            if not self.conn:
                if time.monotonic() - self.started < CONNECT_TIME:
                    self._connect()
                if not self.conn:
                    time.sleep(min(0.1, max(0.0, left)))
            else:
                self._read(min(0.2, max(0.0, left)))
            if until - time.monotonic() <= 0:
                break
        return self.running

    def restart(self, o):
        """
        a new player for what plays from where it is, and what comes after
        it, with @o (its quality, say); a live stream starts again from now
        """
        start = self.cur if self.cur < len(self.tracks) else 0
        items = [t.item for t in self.tracks[start:]]
        again = o.copy()
        again.start = -1 if "live" in items[0].f else int(
            self.tracks[start].pos)
        again.resume = False
        self.finish()
        return Player(self.player, self.lib, items, again)

    def finish(self):
        """
        stop it if it still plays, record the positions in the history;
        (exit status, why nothing played when it ended on its own)
        """
        ended = not self.running
        if self.running and self.conn:
            self.command("quit")
        t0 = time.monotonic()
        while self.running and time.monotonic() - t0 < QUIT_TIME:
            self.poll(0.1)
        if self.running:
            try:
                self.proc.terminate()
                self.status = self.proc.wait(5)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.status = self.proc.wait()
            self.running = False
        if self.conn:
            self.conn.close()
            self.conn = None
        if not platform.WINDOWS:
            try:
                os.unlink(self.addr)
            except OSError:
                pass
        for t in self.tracks if self.lib else ():
            if not t.played or "file" in t.item.f:
                continue
            ln = int(t.len)
            # a live stream has no length: nothing to resume
            try:
                self.lib.hist_record(t.item, int(t.pos) if ln > 0 else 0,
                                     ln if ln > 0 else 0)
            except Exception as e:  # the library: say it, go on
                log.warn(f"history: {e}")
        played = any(t.played for t in self.tracks)
        problem = None
        # it ended on its own, not stopped: say why if nothing played
        if ended and not played:
            problem = why_nothing(self.log_file)
        return self.status or 0, problem


def _short_links(s):
    """@s with each link cut after its site: "https://host/…" """
    return re.sub(r"(://[^/ '\"]*)/[^ '\"]*", "\\1/…", s)


def why_nothing(log_file):
    """
    why mpv played nothing, from its log: the last error, yt-dlp's first
    ("[ytdl_hook] ERROR: [youtube] ...: Sign in to confirm you're not a
    bot"). Lines look like "[   1.234][e][ytdl_hook] text".
    """
    found = ytdl = None
    try:
        text = platform.read_file(log_file, 4 << 20) if log_file else ""
    except (OSError, ValueError):
        text = ""
    for line in text.split("\n"):
        i = line.find("][e][")
        if i < 0:
            continue
        j = line.find("]", i + 5)
        if j < 0 or line[j + 1:j + 2] != " ":
            continue
        msg = line[j + 2:]
        if line[i + 5:i + 14] == "ytdl_hook" and ytdl is None:
            ytdl = msg
        found = msg
    if ytdl is not None:
        found = ytdl
    if found is None:
        return "mpv played nothing (vts doctor checks yt-dlp)"
    return "mpv played nothing: " + _short_links(clean(found.rstrip(" \r")))


# --- downloads ---------------------------------------------------------------

# yt-dlp's numbers, "NA" when it doesn't know
PROGRESS_TAG = "vts-progress "
PROGRESS_TEMPLATE = ("download:" + PROGRESS_TAG +
                     "%(progress.downloaded_bytes)s "
                     "%(progress.total_bytes)s "
                     "%(progress.total_bytes_estimate)s "
                     "%(progress.speed)s %(progress.eta)s")


class Progress:
    def __init__(self):
        self.done = 0.0  # bytes of the file under way
        self.total = 0.0  # its size, 0 if unknown
        self.speed = 0.0  # bytes a second, 0 if unknown
        self.eta = -1  # seconds left, -1 if unknown
        self.files = 0  # files begun (a video and its sound: 2)
        self.finishing = False  # joining, converting: after the downloads
        self.error = ""  # yt-dlp's last error
        self.file = None  # the last file named

    def percent(self):
        return int(self.done * 100 / self.total) if self.total > 0 else None

    def line(self, line):
        """take in one line of what yt-dlp says: whether it changed"""
        line = line.replace("\r", "")
        if line.startswith(PROGRESS_TAG):
            nums = []
            for w in line[len(PROGRESS_TAG):].split()[:5]:
                try:
                    v = float(w)
                    nums.append(v if v >= 0 and not math.isnan(v) else -1)
                except ValueError:
                    nums.append(-1)
            nums += [-1] * (5 - len(nums))
            done, total, estimate, speed, eta = nums
            self.done = done if done > 0 else 0
            self.total = total if total > 0 else (estimate if estimate > 0
                                                  else 0)
            self.speed = speed if speed > 0 else 0
            self.eta = int(eta) if 0 <= eta < 1e7 else -1
            if not self.files:
                self.files = 1
            return True
        if line.startswith("[download] Destination:"):
            self.files += 1
            self.done = self.total = self.speed = 0
            self.eta = -1
            self.file = line[len("[download] Destination:"):].strip()
            return True
        m = re.match(r"\[(Merger|ExtractAudio|VideoConvertor|FixupM3u8)\]",
                     line)
        if m:
            # joining picture and sound, making the MP3...
            self.finishing = True
            for marker in ('Merging formats into "', "Destination: "):
                i = line.find(marker)
                if i >= 0:
                    self.file = line[i + len(marker):].rstrip('"').strip()
            return True
        m = re.match(r'\[download\] (.*) has already been downloaded', line)
        if m:
            self.file = m.group(1)
            return True
        if line.startswith("ERROR: "):
            self.error = clean(line[7:])[:159]
            return True
        return False


def download_args(ytdlp, folder, items, quality, audio_format=None,
                  quiet=False, sound="high"):
    if quality == QUALITY_AUDIO and audio_format and \
            audio_format not in AUDIO_FORMATS:
        raise ValueError(audio_format)
    if sound not in SOUND_QUALITIES:
        raise ValueError(sound)
    a = [ytdlp, "--no-playlist", "-f", ytdl_format(quality), "-o",
         os.path.join(folder, "%(title)s [%(id)s].%(ext)s")]
    sort = ytdl_sort(quality, sound)
    if sort:
        a += ["-S", sort]
    if quiet:
        # one line per step, in words vts reads
        a += ["--newline", "--progress-template", PROGRESS_TEMPLATE]
    if quality == QUALITY_AUDIO:
        a.append("--extract-audio")
        if audio_format:
            # the bitrate of the sound quality (lossless: the best)
            rate = "0" if audio_format in LOSSLESS else _SOUND[sound][1]
            a += ["--audio-format", audio_format, "--audio-quality", rate]
    # nothing after this is an option, whatever it looks like
    a.append("--")
    a += [video_url(it) for it in items]
    return a


def download(ytdlp, folder, items, quality, audio_format=None,
             sound="high"):
    """download @items into @folder, yt-dlp showing it; its exit status"""
    path = platform.find_program(ytdlp)
    if not path:
        raise FileNotFoundError(ytdlp)
    p = subprocess.Popen(download_args(path, folder, items, quality,
                                       audio_format, sound=sound))
    try:
        return p.wait()
    except KeyboardInterrupt:
        p.terminate()
        p.wait()
        raise


class Download:
    """a download in the background, its progress read from yt-dlp"""

    def __init__(self, ytdlp, folder, item, quality, audio_format=None,
                 sound="high"):
        path = platform.find_program(ytdlp)
        if not path:
            raise FileNotFoundError(ytdlp)
        self.item = item
        self.folder = folder
        self.quality = quality
        self.audio_format = audio_format
        self.progress = Progress()
        self.status = None
        self.cancelled = False
        self.started = time.time()
        self._lines = queue.Queue()
        self.proc = subprocess.Popen(
            download_args(path, folder, [item], quality, audio_format, True,
                          sound),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, **platform._popen_flags(True))
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        for raw in self.proc.stdout:
            self._lines.put(raw.decode("utf-8", "replace").rstrip("\n"))
        self._lines.put(None)

    def poll(self):
        """take in what yt-dlp said: whether something changed"""
        changed = False
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                self._reader.join(1)
                self.status = self.proc.wait()
                changed = True
                break
            changed |= self.progress.line(line)
        return changed

    def running(self):
        if self.status is None:
            self.poll()
        return self.status is None

    def cancel(self):
        self.cancelled = True
        if self.status is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def wait(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while self.status is None:
            self.poll()
            if end is not None and time.monotonic() > end:
                return False
            time.sleep(0.05)
        return True

    def ok(self):
        return self.status == 0 and not self.cancelled

    def file(self):
        """the file made, as best yt-dlp said it"""
        f = self.progress.file
        if f and self.audio_format and self.quality == QUALITY_AUDIO:
            base, _ = os.path.splitext(f)
            if os.path.exists(f"{base}.{self.audio_format}"):
                return f"{base}.{self.audio_format}"
        if f and not os.path.isabs(f):
            f = os.path.join(self.folder, f)
        return f


# --- subtitles ---------------------------------------------------------------

INFO_MAX = 64 << 20  # yt-dlp's description of a video


class Subtitle:
    __slots__ = ("code", "name", "automatic")

    def __init__(self, code, name, automatic):
        self.code = code  # for PlayOptions.subs: "fr", "en-orig"
        self.name = name  # "French", "English (auto-generated)"
        self.automatic = automatic  # by YouTube: 1 as heard, 2 translated


def _subtitle(code, name, automatic):
    # the codes go on mpv's command line: checked like the settings
    if not langs_ok(code) or "," in code or len(code) > 20:
        return None
    name = clean(name or code)
    # "English (Original)": what is said, written by YouTube
    if name.endswith(" (Original)") and len(name) > 11:
        name = name[:-11]
    if automatic == 1:
        name += " (auto-generated)"
    elif automatic == 2:
        name += " (auto-translated)"
    return Subtitle(code, name, automatic)


def _track_name(tracks, code):
    lst = tracks.get(code)
    first = lst[0] if isinstance(lst, list) and lst else None
    name = first.get("name") if isinstance(first, dict) else None
    return name if isinstance(name, str) else None


def subtitles(ytdlp, ident, language):
    """
    the subtitles of the video @ident as yt-dlp finds them: those written
    for it, then YouTube's automatic ones in the language spoken and in
    @language (the settings', "fr"); PlayError
    """
    url = video_url(ident)
    path = platform.find_program(ytdlp)
    if not path:
        raise PlayError(f"{ytdlp} is not installed")
    try:
        p = subprocess.run([path, "-J", "--skip-download", "--no-playlist",
                            "--no-warnings", "--", url],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=120,
                           **platform._popen_flags(False))
    except subprocess.TimeoutExpired:
        raise PlayError(f"{ytdlp} took too long") from None
    except OSError as e:
        raise PlayError(f"{ytdlp}: {e.strerror}") from None
    if p.returncode:
        raise PlayError(f"{ytdlp} couldn't read the video")
    try:
        doc = json.loads(p.stdout[:INFO_MAX].decode("utf-8", "replace"))
    except ValueError:
        raise PlayError(f"{ytdlp}'s answer isn't JSON") from None
    if not isinstance(doc, dict):
        raise PlayError(f"{ytdlp}'s answer isn't JSON")
    # the settings' language, without its country: "fr" for "fr-CA"
    lang = re.split(r"[-_]", language or "")[0][:7]
    out = []
    manual = doc.get("subtitles")
    manual = manual if isinstance(manual, dict) else {}
    for code in manual:
        if code != "live_chat":
            s = _subtitle(code, _track_name(manual, code), 0)
            if s:
                out.append(s)
    # of the automatic ones (all languages, translated), two make sense
    autos = doc.get("automatic_captions")
    for code in autos if isinstance(autos, dict) else ():
        orig = len(code) > 5 and code.endswith("-orig")
        # a written one in the language comes first
        if orig or (lang and code == lang and lang not in manual):
            s = _subtitle(code, _track_name(autos, code), 1 if orig else 2)
            if s:
                out.append(s)
    return out

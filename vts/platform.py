# SPDX-License-Identifier: Apache-2.0
"""
What differs between Linux (and other Unix systems) and Windows: where
files go, running programs, talking to mpv.
"""

import os
import shutil
import subprocess
import sys
import time

WINDOWS = os.name == "nt"


# --- folders -----------------------------------------------------------------

def _home():
    home = os.environ.get("USERPROFILE" if WINDOWS else "HOME", "")
    if WINDOWS and not home:
        home = os.environ.get("HOME", "")
    return home if home and os.path.isabs(home) else None


def _xdg(var, fallback):
    """$VAR/vts if $VAR is an absolute path, else $HOME/@fallback/vts"""
    base = os.environ.get(var, "")
    # the specification says to ignore relative paths
    if base.startswith("/"):
        return base.rstrip("/") + "/vts"
    home = _home()
    if not home:
        return None
    return f"{home}/{fallback}/vts"


def _windows(var, *more):
    base = os.environ.get(var, "")
    if not base or not os.path.isabs(base):
        home = _home()
        if not home:
            return None
        base = os.path.join(home, "AppData",
                            "Roaming" if var == "APPDATA" else "Local")
    return os.path.join(base, "vts", *more)


def config_dir():
    """where the settings file is, or None without a home folder"""
    if WINDOWS:
        return _windows("APPDATA")
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir():
    """where the library is"""
    if WINDOWS:
        return _windows("LOCALAPPDATA")
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir():
    """what can be fetched again: thumbnails, rules, logs"""
    if WINDOWS:
        return _windows("LOCALAPPDATA", "cache")
    return _xdg("XDG_CACHE_HOME", ".cache")


def runtime_dir():
    """for mpv's socket: $XDG_RUNTIME_DIR/vts, else the cache"""
    base = os.environ.get("XDG_RUNTIME_DIR", "")
    if not WINDOWS and base.startswith("/"):
        path = base.rstrip("/") + "/vts"
    else:
        path = cache_dir()
    if path:
        mkdirs(path)
    return path


def downloads_dir():
    """the usual Downloads folder, made if missing; None without a home"""
    base = os.environ.get("XDG_DOWNLOAD_DIR", "")
    if not WINDOWS and base.startswith("/"):
        path = base
    else:
        home = _home()
        if not home:
            return None
        path = os.path.join(home, "Downloads")
    mkdirs(path)
    return path


def home():
    return _home()


def mkdirs(path):
    """make @path and its parents (private to the user); OSError if it fails"""
    os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.isdir(path):
        raise NotADirectoryError(path)


# --- files -------------------------------------------------------------------

def read_file(path, max_size, binary=False):
    """the content of @path; OSError, or ValueError when over @max_size"""
    with open(path, "rb") as f:
        data = f.read(max_size + 1)
    if len(data) > max_size:
        raise ValueError(f"{path}: larger than {max_size} bytes")
    return data if binary else data.decode("utf-8", "replace")


def write_atomic(path, data, mode=0o600):
    """@path replaced by @data at once: never half written"""
    if isinstance(data, str):
        data = data.encode()
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC |
                     getattr(os, "O_BINARY", 0), mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def open_append(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8", errors="replace")


def file_stamp(path):
    """(mtime, size) of @path; OSError"""
    st = os.stat(path)
    return int(st.st_mtime), st.st_size


def file_replaceable(path):
    """whether this user can replace @path (and so update it)"""
    d = os.path.dirname(path) or "."
    return os.access(path, os.W_OK) and os.access(d, os.W_OK)


def prune_dir(path, age):
    """remove the files of @path older than @age seconds; how many"""
    now = time.time()
    n = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for e in entries:
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                continue
            if now - e.stat(follow_symlinks=False).st_mtime > age:
                os.unlink(e.path)
                n += 1
        except OSError:
            pass
    return n


# --- programs ----------------------------------------------------------------

# where Windows installers put programs without adding them to the PATH
_WINDOWS_PLACES = {
    "mpv": (r"%ProgramFiles%\MPV Player", r"%ProgramFiles%\mpv",
            r"%ProgramFiles(x86)%\MPV Player", r"%LOCALAPPDATA%\Programs\mpv",
            r"%USERPROFILE%\scoop\shims", r"%ProgramData%\chocolatey\bin"),
}


def find_program(name):
    """the path of the program @name (or a path to one), or None"""
    if os.sep in name or "/" in name:
        if os.path.isfile(name) and os.access(name, os.X_OK):
            return name
        if WINDOWS:
            found = shutil.which(name)
            return found
        return None
    found = shutil.which(name)
    if found or not WINDOWS:
        return found
    base = name[:-4] if name.lower().endswith(".exe") else name
    for place in _WINDOWS_PLACES.get(base.lower(), ()):
        var, _, rest = place[1:].partition("%")
        if not os.environ.get(var):
            continue
        path = os.path.join(os.environ[var], *rest.strip("\\").split("\\"),
                            base + ".exe")
        if os.path.isfile(path):
            return path
    return None


def _popen_flags(detach):
    kw = {}
    if WINDOWS:
        flags = subprocess.CREATE_NO_WINDOW
        if detach:
            flags |= subprocess.CREATE_NEW_PROCESS_GROUP
        kw["creationflags"] = flags
    elif detach:
        kw["start_new_session"] = True
    return kw


def spawn(argv, quiet=False, capture=False, capture_err=False, detach=False,
          stdin_null=True):
    """start @argv; OSError when it can't be"""
    out = subprocess.PIPE if capture else (subprocess.DEVNULL if quiet
                                            else None)
    if capture and capture_err:
        err = subprocess.STDOUT
    else:
        err = subprocess.DEVNULL if quiet else None
    return subprocess.Popen(
        argv, stdin=subprocess.DEVNULL if (quiet or stdin_null) else None,
        stdout=out, stderr=err, **_popen_flags(detach))


def run_capture(argv, max_size=65536, timeout=60):
    """(exit status, what @argv printed on stdout); OSError"""
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=timeout, **_popen_flags(False))
    except subprocess.TimeoutExpired:
        return -1, ""
    return p.returncode, p.stdout[:max_size].decode("utf-8", "replace")


def self_command():
    """how to run vts again, for timers and scheduled tasks"""
    env = os.environ.get("VTS_PROGRAM", "")
    if env and os.path.isabs(env):
        return [env]
    argv0 = sys.argv[0] if sys.argv else ""
    if os.path.basename(argv0) in ("vts", "vts.exe") and \
            os.path.isfile(argv0):
        return [os.path.realpath(argv0)]
    exe = shutil.which("vts")
    if exe:
        return [os.path.realpath(exe)]
    py = sys.executable
    if WINDOWS:
        # no console window for what runs in the background
        w = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(w):
            py = w
    return [py, "-m", "vts"]


# --- the terminal ------------------------------------------------------------

def term_write(text):
    """
    @text straight to the terminal, past curses: pictures, the clipboard.
    On Windows to the screen buffer shown (curses draws in one of its own,
    not in the one of stdout), as UTF-16 whatever the code page.
    """
    if not WINDOWS:
        sys.stdout.flush()
        data = text.encode()
        try:
            while data:
                data = data[os.write(sys.stdout.fileno(), data):]
        except OSError:
            pass
        return
    _console_write(text)


def _console_write(text):
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    # GENERIC_READ | GENERIC_WRITE, shared, OPEN_EXISTING
    got = k32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)
    if not got or got == wintypes.HANDLE(-1).value:
        return
    h = wintypes.HANDLE(got)  # whole, not cut to 32 bits
    mode = wintypes.DWORD()
    old = None
    try:
        if not k32.GetConsoleMode(h, ctypes.byref(mode)):
            return
        # the escapes obeyed: ENABLE_VIRTUAL_TERMINAL_PROCESSING, which
        # needs ENABLE_PROCESSED_OUTPUT (curses turns both off); never
        # written as text when the console won't
        want = mode.value | 1 | 4
        if want != mode.value:
            if not k32.SetConsoleMode(h, want):
                return
            old = mode.value
        done = wintypes.DWORD()
        for i in range(0, len(text), 16384):
            part = text[i:i + 16384]
            # its length in UTF-16 units (two for an emoji)
            n = len(part.encode("utf-16-le")) // 2
            k32.WriteConsoleW(h, part, n, ctypes.byref(done), None)
    finally:
        if old is not None:
            # as curses had it
            k32.SetConsoleMode(h, old)
        k32.CloseHandle(h)


def term_cols(stream=None):
    """columns of the terminal @stream is (default stdout), 0 if none"""
    stream = stream or sys.stdout
    try:
        if not stream.isatty():
            return 0
        cols = os.get_terminal_size(stream.fileno()).columns
    except (OSError, ValueError, AttributeError):
        return 80
    return cols or 80


# --- talking to mpv ----------------------------------------------------------

def ipc_address(name):
    """where mpv listens for vts: a socket file, or a named pipe on Windows"""
    if WINDOWS:
        return rf"\\.\pipe\vts-{name}-{os.getpid()}"
    d = runtime_dir()
    if not d:
        return None
    return os.path.join(d, f"{name}-{os.getpid()}.sock")


class IpcConnection:
    """a connection to mpv's JSON IPC, lines of JSON both ways"""

    def __init__(self, address):
        self.address = address
        self._sock = None
        self._pipe = None
        self._buf = b""
        if WINDOWS:
            self._pipe = open(address, "r+b", buffering=0)
        else:
            import socket
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.connect(address)
            except OSError:
                s.close()
                raise
            s.setblocking(False)
            self._sock = s

    def send(self, data):
        if self._sock:
            self._sock.setblocking(True)
            try:
                self._sock.sendall(data)
            finally:
                self._sock.setblocking(False)
        else:
            self._pipe.write(data)

    def read_lines(self, timeout=0.0):
        """the whole lines that came, waiting up to @timeout seconds"""
        if self._sock:
            import select
            r, _, _ = select.select([self._sock], [], [], timeout)
            while r:
                try:
                    chunk = self._sock.recv(65536)
                except BlockingIOError:
                    break
                if not chunk:
                    raise ConnectionError("mpv went away")
                self._buf += chunk
                if len(self._buf) > 4 * 1024 * 1024:
                    raise ConnectionError("too much from mpv")
                r, _, _ = select.select([self._sock], [], [], 0)
        else:
            self._buf += _pipe_read(self._pipe, timeout)
        *lines, self._buf = self._buf.split(b"\n")
        return lines

    def close(self):
        for f in (self._sock, self._pipe):
            if f:
                try:
                    f.close()
                except OSError:
                    pass
        self._sock = self._pipe = None


def _pipe_read(pipe, timeout):
    """what is waiting in a Windows named pipe, without blocking long"""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    handle = msvcrt.get_osfhandle(pipe.fileno())
    avail = wintypes.DWORD()
    end = time.monotonic() + timeout
    data = b""
    while True:
        if not k32.PeekNamedPipe(handle, None, 0, None,
                                 ctypes.byref(avail), None):
            raise ConnectionError("mpv went away")
        if avail.value:
            data += pipe.read(avail.value)
            continue
        if data or time.monotonic() >= end:
            return data
        time.sleep(0.02)

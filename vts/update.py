# SPDX-License-Identifier: Apache-2.0
"""
Keeping up with YouTube: newer rules from vts' repository, once a day,
and yt-dlp's own updates, once a week.
"""

import json
import os
import time

from vts import http, log, platform, util
from vts.rules import BadRules, Rules, builtin_text

RULES_URL = ("https://raw.githubusercontent.com/ifupauline/vts/main/vts/"
             "youtube.json")
RULES_MAX = 1024 * 1024
RULES_EVERY = 24 * 3600  # seconds between checks
YTDLP_EVERY = 7 * 24 * 3600
YTDLP_TOO_OLD = 180  # days
YTDLP_OLD = 60
RULES_TIMEOUT = 15.0
VERSION_MAX = 4096


def rules_url():
    env = os.environ.get("VTS_RULES_URL", "")
    if env.startswith(("https://", "http://")):
        return env
    # the tests play YouTube: never the real repository from them
    base = os.environ.get("VTS_BASE_URL", "")
    if base.startswith(("https://", "http://")):
        return base + "/rules.json"
    return RULES_URL


def rules_cache_path():
    d = platform.cache_dir()
    if not d:
        return None
    platform.mkdirs(d)
    return os.path.join(d, "rules.json")


def best_rules():
    """(the rules to use, whether they came from an update)"""
    r = Rules(builtin_text())
    try:
        p = rules_cache_path()
        if not p:
            return r, False
        data = platform.read_file(p, RULES_MAX, binary=True)
    except (OSError, ValueError):
        return r, False
    try:
        kept = Rules(data)
    except BadRules as e:
        log.warn(f"{p}: {e}; the built-in rules are used")
        return r, False
    if kept.revision > r.revision:
        return kept, True
    return r, False


def _same_shape(ref, doc, name="the file"):
    """
    None when @doc has everything @ref has, of the same types, at any
    depth (more is fine: newer rules may know more), else what is missing
    """
    def kind(v):
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, (int, float)):
            return "number"
        return type(v).__name__

    if kind(ref) != kind(doc):
        return f"{name}: not of the expected type"
    if not isinstance(ref, dict):
        return None
    for k, v in ref.items():
        if k not in doc:
            return f"{k} is missing"
        why = _same_shape(v, doc[k], k)
        if why:
            return f"{why} (in {k})"
    return None


def take_rules(data, current):
    """
    the rules in @data if they are good and newer than revision @current
    (kept in the cache), None if not newer; BadRules (or OSError)
    """
    new = Rules(data)
    # everything the program reads must be there
    why = _same_shape(json.loads(builtin_text()), new.doc)
    if why:
        raise BadRules(why)
    if new.revision <= current:
        return None
    try:
        platform.write_atomic(rules_cache_path(), data)
    except (OSError, TypeError) as e:
        raise BadRules(f"couldn't keep them: {getattr(e, 'strerror', e)}") \
            from None
    return new


class UpdateError(Exception):
    pass


def update_rules(current):
    """newer rules from the repository, or None; UpdateError, BadRules"""
    url = rules_url()
    r = http.fetch(http.Request(url, max_size=RULES_MAX,
                                timeout=RULES_TIMEOUT, retries=1))
    if r.error:
        raise UpdateError(r.message)
    if r.status != 200:
        what = "nothing there" if r.status == 404 else "refused"
        raise UpdateError(f"{url}: {what} (HTTP {r.status})")
    return take_rules(r.body, current)


# --- now and then ------------------------------------------------------------

def _stamp_path(what):
    d = platform.cache_dir()
    if not d:
        return None
    platform.mkdirs(d)
    return os.path.join(d, f"checked-{what}")


def last(what):
    """when @what was last checked (seconds since 1970), 0 for never"""
    try:
        p = _stamp_path(what)
        t = platform.read_file(p, 64).splitlines()
    except (OSError, ValueError, TypeError):
        return 0
    v = util.parse_long(t[0].strip(" \t") if t else "", 0, 99999999999)
    return v or 0


def due(what, every):
    now = int(time.time())
    when = last(what)
    # a clock set back counts as due too
    return when == 0 or now - when >= every or now < when


def done(what):
    try:
        platform.write_atomic(_stamp_path(what), f"{int(time.time())}\n")
    except (OSError, TypeError):
        log.dbg(f"couldn't note the {what} check")


# --- yt-dlp ------------------------------------------------------------------

def program_version(prog):
    """the first line of `@prog --version`, or None"""
    try:
        status, out = platform.run_capture([prog, "--version"], VERSION_MAX)
    except OSError:
        return None
    line = out.splitlines()[0] if out else ""
    if status != 0 or not line:
        return None
    return line


def ytdlp_age(version):
    """the age in days of yt-dlp @version ("2026.11.10"), or None"""
    parts = version.split(".")
    if len(parts) < 3:
        return None
    try:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not 2000 <= y <= 9999 or not 1 <= m <= 12 or not 1 <= d <= 31:
        return None
    return int(time.time()) // 86400 - util.days_from_civil(y, m, d)


def ytdlp_update(prog, background=False):
    """
    have yt-dlp update itself: (path, status, output), or a Popen in the
    background; PermissionError when the system installed it, OSError
    """
    path = platform.find_program(prog)
    if not path:
        raise FileNotFoundError(prog)
    # installed by the system: its packages update it, not vts
    if not platform.file_replaceable(path):
        raise PermissionError(path)
    if background:
        return platform.spawn([path, "--update"], quiet=True, detach=True)
    status, out = platform.run_capture([path, "--update"], 65536, timeout=300)
    return path, status, out


def ytdlp_version(path):
    """yt-dlp's version, from the cache while the file stays the same"""
    try:
        mtime, size = platform.file_stamp(path)
    except OSError:
        return None
    key = f"{mtime} {size} {path}\n"
    d = platform.cache_dir()
    cache = os.path.join(d, "yt-dlp-version") if d else None
    if cache:
        try:
            t = platform.read_file(cache, 4096)
            if t.startswith(key):
                v = t[len(key):].split("\n")[0]
                if v:
                    return v
        except (OSError, ValueError):
            pass
    v = program_version(path)
    if v and cache:
        try:
            platform.mkdirs(d)
            platform.write_atomic(cache, f"{key}{v}\n")
        except OSError:
            log.dbg("couldn't keep yt-dlp's version")
    return v


def ytdlp_problem(ytdlp):
    """what is wrong with yt-dlp, or None"""
    path = platform.find_program(ytdlp)
    if not path:
        return f"{ytdlp} is not installed: videos can't be played"
    v = ytdlp_version(path)
    if not v:
        return f"{path} doesn't run: reinstall it"
    age = ytdlp_age(v)
    if age is not None and age > YTDLP_TOO_OLD:
        return (f"yt-dlp {v} is {age} days old, too old to play YouTube: "
                "vts doctor says how to fix it")
    return None


def can_play(player, ytdlp):
    """what keeps videos from playing, or None"""
    if not platform.find_program(player):
        return f"{player} is not installed: nothing can play"
    return ytdlp_problem(ytdlp)


def upkeep(cfg, yt, quiet=False):
    """the updates that are due, as the app and `vts check` do them"""
    if cfg.offline or not cfg.updates:
        return
    if due("rules", RULES_EVERY):
        try:
            new = update_rules(yt.rules.revision)
            if new:
                yt.set_rules(new)
                log.info(f"new rules: revision {new.revision}")
            done("rules")
        except (UpdateError, BadRules) as e:
            log.dbg(f"rules update: {e}")
            done("rules")
    if due("yt-dlp", YTDLP_EVERY):
        try:
            ytdlp_update(cfg.downloader, background=True)
        except OSError:
            pass
        done("yt-dlp")

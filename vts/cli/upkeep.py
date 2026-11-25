# SPDX-License-Identifier: Apache-2.0
"""check, update, doctor (and notified, behind the notifications)"""

import os
import subprocess
import sys
import time

from vts import __version__, check, log, notify, platform, update, util
from vts.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, Usage, out, print_items
from vts.cli import print_json
from vts.library import LibError, Library
from vts.rules import BadRules
from vts.youtube import Client, YouTubeError

SERVICE = "vts-check.service"
TIMER = "vts-check.timer"
TASK = "vts-check"

CHECK_USAGE = """\
Usage: vts check [OPTION]...
Look for new videos of your favourite channels, print them and
send a desktop notification for each; then say which of your
favourites are live now, on YouTube, Twitch and SOOP.

  -q, --quiet          print nothing (for a timer), and don't
                       look for live streams
  -n, --no-notify      no desktop notifications
  -j, --json           print the new videos as JSON (no live)
      --no-live        don't look for live streams
      --install [MIN]  check every MIN minutes (30 by default)
                       with a {how}
      --uninstall      remove it
  -h, --help           show this help
"""


def check_usage():
    return CHECK_USAGE.format(how="scheduled task of Windows"
                              if platform.WINDOWS else "systemd user timer")


def maintain(cfg, yt, background=False):
    """the updates that are due: the rules daily, yt-dlp weekly"""
    if cfg.offline or not cfg.updates:
        return
    if update.due("rules", update.RULES_EVERY):
        update.done("rules")
        try:
            new = update.update_rules(yt.rules.revision)
            if new:
                log.info(f"rules updated to revision {new.revision}")
                yt.set_rules(new)
        except (update.UpdateError, BadRules) as e:
            log.dbg(f"rules update: {e}")
    if update.due("yt-dlp", update.YTDLP_EVERY):
        update.done("yt-dlp")
        try:
            r = update.ytdlp_update(cfg.downloader, background)
            if not background and r[1]:
                log.dbg(f"{r[0]} --update: exit status {r[1]}")
        except OSError:
            pass


# --- the timer ---------------------------------------------------------------

def _unit_quoted(path):
    """@path for systemd: quoted, with % doubled"""
    if any(c in path for c in '"\\\n'):
        raise ValueError(path)
    return '"' + path.replace("%", "%%") + '"'


def _systemctl(*args):
    try:
        return subprocess.run(["systemctl", "--user", *args],
                              stdin=subprocess.DEVNULL).returncode
    except OSError:
        return 1


def _unit_dir():
    d = platform.config_dir()
    if not d:
        raise OSError(2, "no home folder")
    # ~/.config/vts -> ~/.config/systemd/user
    d = os.path.join(os.path.dirname(d), "systemd", "user")
    platform.mkdirs(d)
    return d


def install(minutes, config_file):
    self = platform.self_command()
    if platform.WINDOWS:
        # pythonw: no console window every time it runs
        w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if os.path.exists(w):
            self = [w, "-m", "vts"]
        return _install_windows(minutes, config_file, self)
    try:
        d = _unit_dir()
        cmd = " ".join(_unit_quoted(w) for w in self)
        if config_file:
            cmd += " -c " + _unit_quoted(os.path.abspath(config_file))
        platform.write_atomic(
            os.path.join(d, SERVICE),
            "[Unit]\nDescription=New videos of vts favourites\n"
            "\n[Service]\nType=oneshot\n"
            f"ExecStart={cmd} check --quiet\n", 0o644)
        platform.write_atomic(
            os.path.join(d, TIMER),
            "[Unit]\nDescription=Look for new videos of vts favourites\n"
            "\n[Timer]\nOnBootSec=2min\n"
            f"OnUnitActiveSec={minutes}min\n"
            "RandomizedDelaySec=2min\nPersistent=true\n"
            "\n[Install]\nWantedBy=timers.target\n", 0o644)
    except ValueError:
        log.err("can't set up the timer: the path of vts has quotes")
        return EXIT_FAIL
    except OSError as e:
        log.err(f"can't set up the timer: {e.strerror}")
        return EXIT_FAIL
    if _systemctl("daemon-reload") or _systemctl("enable", "--now", TIMER):
        log.err(f"systemctl couldn't start the timer (files in {d})")
        return EXIT_FAIL
    out(f"vts checks for new videos every {minutes} minutes")
    return EXIT_OK


def _install_windows(minutes, config_file, self):
    args = list(self[1:])
    if config_file:
        args += ["-c", os.path.abspath(config_file)]
    args += ["check", "--quiet"]
    tr = subprocess.list2cmdline([self[0]]) + " " + \
        subprocess.list2cmdline(args)
    try:
        r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE",
                            "/MO", str(minutes), "/TN", TASK, "/TR", tr],
                           stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as e:
        log.err(f"schtasks: {e.strerror}")
        return EXIT_FAIL
    if r.returncode:
        log.err("schtasks couldn't make the task: " +
                r.stderr.decode(errors="replace").strip())
        return EXIT_FAIL
    out(f"vts checks for new videos every {minutes} minutes")
    return EXIT_OK


def uninstall():
    if platform.WINDOWS:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK],
                       stdin=subprocess.DEVNULL, capture_output=True)
        out("the scheduled task is removed")
        return EXIT_OK
    try:
        d = _unit_dir()
    except OSError as e:
        log.err(e.strerror)
        return EXIT_FAIL
    _systemctl("disable", "--now", TIMER)
    for name in (TIMER, SERVICE):
        try:
            os.unlink(os.path.join(d, name))
        except OSError:
            pass
    _systemctl("daemon-reload")
    out("the timer is removed")
    return EXIT_OK


def timer_installed():
    """where the timer (the scheduled task) is, or None"""
    if platform.WINDOWS:
        try:
            r = subprocess.run(["schtasks", "/Query", "/TN", TASK],
                               stdin=subprocess.DEVNULL, capture_output=True,
                               **platform._popen_flags(False))
        except OSError:
            return None
        return f"the scheduled task {TASK}" if r.returncode == 0 else None
    d = platform.config_dir()
    if not d:
        return None
    p = os.path.join(os.path.dirname(d), "systemd", "user", TIMER)
    return p if os.path.isfile(p) else None


def cmd_check(cfg, argv):
    import getopt
    try:
        opts, args = getopt.gnu_getopt(argv, "qnjh",
                                       ["quiet", "no-notify", "json",
                                        "no-live", "install", "uninstall",
                                        "help"])
    except getopt.GetoptError:
        sys.stderr.write(check_usage())
        raise Usage() from None
    quiet = as_json = do_install = no_live = False
    notify_on = cfg.notifications
    for o, _ in opts:
        if o in ("-q", "--quiet"):
            quiet = True
        elif o in ("-n", "--no-notify"):
            notify_on = False
        elif o in ("-j", "--json"):
            as_json = True
        elif o == "--no-live":
            no_live = True
        elif o == "--install":
            do_install = True
        elif o == "--uninstall":
            return uninstall()
        else:
            sys.stdout.write(check_usage())
            return EXIT_OK
    if do_install:
        minutes = 30
        if args:
            minutes = util.parse_long(args.pop(0), 5, 24 * 60)
            if minutes is None:
                print("vts check --install: 5 to 1440 minutes",
                      file=sys.stderr)
                return EXIT_USAGE
        if args:
            sys.stderr.write(check_usage())
            return EXIT_USAGE
        return install(minutes, cfg.config_file)
    if args:
        sys.stderr.write(check_usage())
        return EXIT_USAGE
    try:
        lib = Library()
    except (LibError, OSError) as e:
        log.err(str(e))
        return EXIT_FAIL
    with lib:
        yt = Client(cfg)
        maintain(cfg, yt)
        try:
            c = check.run(yt, lib)
        except LibError as e:
            log.err(str(e))
            return EXIT_FAIL
        if notify_on and c.fresh.items:
            try:
                check.tell(lib, c, cfg.notifier)
            except FileNotFoundError:
                log.warn(f"{cfg.notifier} is not installed: no desktop "
                         "notifications")
        # who is live: asked by hand only (a timer prints nothing)
        lv = None
        if not (quiet or as_json or no_live):
            try:
                lv = check.live(yt, lib)
            except LibError as e:
                log.err(str(e))
    if as_json:
        print_json(c.fresh.to_json())
    elif not quiet:
        print_items(c.fresh.items)
    if not quiet and not as_json and platform.term_cols() and \
            not c.fresh.items:
        out(f"Nothing new from {c.channels} channel"
            f"{'' if c.channels == 1 else 's'}.")
    if not quiet and c.first:
        print(f"vts: {c.first} channel{'' if c.first == 1 else 's'} checked "
              "for the first time: from now on, their new videos",
              file=sys.stderr)
    if lv:
        print_live(lv)
    # a failure only when nothing at all could be read
    return EXIT_FAIL if c.channels and c.failed == c.channels else EXIT_OK


def print_live(lv):
    """the live streams of the favourites, by site; on a terminal with a
    heading for each site, else lines as search prints them"""
    from vts import sites
    cols = platform.term_cols()
    any_live = False
    for key in sites.SITES:
        found = lv.found[key]
        if not found:
            continue
        any_live = True
        if cols:
            out()
            out(f"Live now on {sites.NAMES[key]}:")
        print_items(found, cols)
        if cols:
            for ident, n in lv.more.items():
                if any(it["channel_id"] == ident for it in found):
                    name = next(it["channel"] for it in found
                                if it["channel_id"] == ident)
                    out(f"  and {n} more live on {name}")
    if cols and not any_live and lv.channels:
        out("None of your favourites is live now.")


def cmd_notified(cfg, argv):
    """behind a notification with buttons: wait for a click"""
    if len(argv) != 4:
        return EXIT_USAGE
    return notify.wait_action(cfg, *argv)


# --- vts update --------------------------------------------------------------

UPDATE_USAGE = """\
Usage: vts update [--rules | --yt-dlp | --vts]
Update now what vts updates on its own once in a while:
the rules that tell where things are in YouTube's, Twitch's
and SOOP's answers (from vts' repository), yt-dlp when you
installed it yourself (yt-dlp --update), and vts itself with
Streamlink (installed as this command ends).

  --rules   only the rules
  --yt-dlp  only yt-dlp
  --vts     only vts and Streamlink
"""


def update_rules(cfg):
    yt = Client(cfg)
    before = yt.rules.revision
    try:
        new = update.update_rules(before)
    except update.UpdateError as e:
        update.done("rules")
        log.err(f"rules: {e}")
        return False
    except BadRules as e:
        update.done("rules")
        log.err(f"rules: {e} (not used)")
        return False
    update.done("rules")
    if new:
        out(f"rules: revision {new.revision} (was {before})")
    else:
        out(f"rules: revision {before}, up to date")
    return True


def update_ytdlp(cfg):
    try:
        path, status, text = update.ytdlp_update(cfg.downloader)
    except FileNotFoundError:
        update.done("yt-dlp")
        log.err(f"{cfg.downloader}: not installed")
        return False
    except PermissionError as e:
        update.done("yt-dlp")
        # not ours to replace: the system's packages do it
        out(f"{cfg.downloader}: {e.args[0]} belongs to the system, update "
            "it with its packages")
        return True
    except OSError as e:
        update.done("yt-dlp")
        log.err(f"{cfg.downloader}: {e.strerror}")
        return False
    update.done("yt-dlp")
    sys.stdout.write(text)
    if status:
        log.err(f"{path} --update: exit status {status}")
        return False
    return True


def cmd_update(cfg, argv):
    import getopt
    try:
        opts, args = getopt.gnu_getopt(argv, "h", ["rules", "yt-dlp", "vts",
                                                   "help"])
    except getopt.GetoptError:
        sys.stderr.write(UPDATE_USAGE)
        return EXIT_USAGE
    rules = ytdlp = itself = True
    for o, _ in opts:
        if o == "--rules":
            ytdlp = itself = False
        elif o == "--yt-dlp":
            rules = itself = False
        elif o == "--vts":
            rules = ytdlp = False
        else:
            sys.stdout.write(UPDATE_USAGE)
            return EXIT_OK
    if args or len(opts) > 1:
        sys.stderr.write(UPDATE_USAGE)
        return EXIT_USAGE
    if cfg.offline:
        log.err("update: not with --offline")
        return EXIT_FAIL
    ok = True
    if rules:
        ok &= update_rules(cfg)
        ok &= update_sites()
    if ytdlp:
        ok &= update_ytdlp(cfg)
    if itself:
        ok &= update_itself()
    return EXIT_OK if ok else EXIT_FAIL


def update_sites():
    """twitch.json and soop.json"""
    ok = True
    for name, got in update.update_sites().items():
        if isinstance(got, str):
            log.err(f"{name}.json: {got}")
            ok = False
        elif got:
            out(f"{name}.json: revision {got}")
        else:
            out(f"{name}.json: up to date")
    update.done("sites")
    return ok


def update_itself():
    """vts and Streamlink, by a helper started as this command ends"""
    from vts import selfupdate
    commit, ok = selfupdate.installed()
    if not ok:
        out("vts: not an installed copy (run from its source, or not "
            "yours to change): not updated")
        return True
    update.done("vts")
    new = selfupdate.latest()
    if new is None:
        log.err("vts: GitHub didn't say which version is the latest")
        ok = False
    elif new == commit:
        out(f"vts: {new[:7]}, up to date")
    else:
        try:
            platform.write_atomic(selfupdate._data("vts-pending"), new + "\n")
        except (OSError, TypeError) as e:
            log.err(f"vts: {e}")
            return False
        out(f"vts: {new[:7]} is installed in the background now (it was "
            f"{commit[:7] if commit else 'unknown'})")
    if selfupdate.after_exit(force=True):
        out("Streamlink: updated in the background now")
    return ok


# --- vts doctor --------------------------------------------------------------

# what the doctor tries: a video that stays, a channel with a feed
PROBE_QUERY = "linus torvalds"
PROBE_VIDEO = "dQw4w9WgXcQ"
PROBE_CHANNEL = "UCsXVk37bltHxD1rDPwtNM8Q"
PROBE_TYPED = "linus tor"
PROBE_THUMB_TIME = 10.0

OK, INFO, WARN, BAD = range(4)
_MARKS = ("ok", "--", "!!", "XX")


class Doctor:
    """the checks, each a line; also what the Settings screen shows"""

    def __init__(self, write=None):
        self.problems = 0
        self.warnings = 0
        self.lines = []
        self.write = write

    def say(self, verdict, what, text):
        from vts.text import clean
        # some of it comes from YouTube or other programs
        text = clean(text)
        self.lines.append((verdict, what, text))
        if self.write:
            self.write(f"  {_MARKS[verdict]}  {what:<12} {text}")
        if verdict == BAD:
            self.problems += 1
        elif verdict == WARN:
            self.warnings += 1

    def summary(self):
        p, w = self.problems, self.warnings
        if p:
            return (f"{p} problem{'s' if p > 1 else ''}, {w} warning"
                    f"{'' if w == 1 else 's'}.")
        if w:
            return f"Works, {w} warning{'s' if w > 1 else ''}."
        return "Everything works."


def _ago(when):
    s = int(time.time()) - when
    if when <= 0:
        return "never"
    if s < 3600:
        return f"{0 if s < 60 else s // 60} min ago"
    if s < 2 * 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} days ago"


def doctor_streamlink(d):
    """Streamlink, for Twitch's and SOOP's live streams: it comes with vts"""
    from vts import player
    try:
        status, text = platform.run_capture(
            player.streamlink_command() + ["--version"], timeout=60)
    except OSError:
        status, text = -1, ""
    if status == 0 and text.strip():
        d.say(OK, "streamlink", text.strip().splitlines()[0])
    else:
        d.say(WARN, "streamlink", "missing: Twitch's and SOOP's live streams "
              "can't play; reinstall vts (pipx reinstall vts)")


def doctor_local(d, cfg):
    from vts import config
    p = config.default_path(cfg)
    if p and os.path.isfile(p):
        d.say(OK, "settings", p)
    else:
        d.say(INFO, "settings", f"{p or 'no home folder:'} not found, "
              "defaults used")
    try:
        with Library() as lib:
            favs = len(lib.favs())
            lists = len(lib.lists())
            d.say(OK, "library", f"{lib.file}: {favs} favourite channel"
                  f"{'' if favs == 1 else 's'}, {lists} playlist"
                  f"{'' if lists == 1 else 's'}")
    except (LibError, OSError) as e:
        d.say(BAD, "library", str(e))
    # programs
    where = platform.find_program(cfg.player)
    if not where:
        d.say(BAD, "player", f"{cfg.player} is not installed: nothing plays")
    else:
        v = update.program_version(where)
        if not v:
            d.say(WARN, "player", f"{where} doesn't say its version")
        else:
            # "mpv v0.38.0 Copyright © 2000-2024 ..." -> "mpv v0.38.0"
            d.say(OK, "player", v.split(" Copyright")[0])
    where = platform.find_program(cfg.downloader)
    if not where:
        d.say(BAD, "yt-dlp", f"{cfg.downloader} is not installed: videos "
              "can't be played")
    else:
        v = update.program_version(where)
        if not v:
            d.say(BAD, "yt-dlp", f"{where} doesn't run")
        else:
            age = update.ytdlp_age(v)
            if age is not None and age > update.YTDLP_OLD:
                how = ("vts update --yt-dlp"
                       if platform.file_replaceable(where) else
                       "install yt-dlp's own release (see the guide)")
                d.say(BAD if age > update.YTDLP_TOO_OLD else WARN, "yt-dlp",
                      f"{v} ({where}) is {age} days old: {how}")
            else:
                d.say(OK, "yt-dlp", v)
    # yt-dlp needs a JavaScript runtime for YouTube's players
    where = platform.find_program("deno")
    if where:
        v = update.program_version(where) or where
        d.say(OK, "deno", v.split(" (")[0])
    else:
        d.say(WARN, "deno", "not installed: yt-dlp needs it (or another "
              "JavaScript runtime) for many YouTube videos; see deno.com")
    # yt-dlp needs it to join picture and sound, and to make MP3s
    where = platform.find_program("ffmpeg")
    if not where:
        d.say(WARN, "ffmpeg", "not installed: downloads come as one file, "
              "often 360p, and not as MP3, M4A, Opus or FLAC")
    else:
        d.say(OK, "ffmpeg", where)
    doctor_streamlink(d)

    if not cfg.notifications:
        d.say(INFO, "notifier", "notifications are off")
    elif platform.WINDOWS:
        if notify.available(cfg.notifier):
            d.say(OK, "notifier", "Windows notifications")
        else:
            d.say(WARN, "notifier", "PowerShell is missing: no "
                  "notifications")
    else:
        where = platform.find_program(cfg.notifier)
        if not where:
            d.say(WARN, "notifier", f"{cfg.notifier} is not installed: no "
                  "notifications")
        else:
            d.say(OK, "notifier", where)
    t = timer_installed()
    if t:
        d.say(OK, "timer", f"new videos checked by {t}")
    else:
        d.say(INFO, "timer", "not set up: vts check --install")


def doctor_rules(d, cfg, yt):
    d.say(OK, "rules", f"revision {yt.rules.revision}, "
          f"{'updated' if yt.rules_updated else 'built in'}; updates "
          + ("off (--offline)" if cfg.offline else "on" if cfg.updates
             else "off") + f", checked {_ago(update.last('rules'))}")


def doctor_youtube(d, yt):
    from vts import feed, search, video
    try:
        page = search.search(yt, PROBE_QUERY)
        if not page.items:
            d.say(BAD, "search", "no results read: YouTube changed, vts "
                  "update")
        else:
            d.say(OK, "search", f"{len(page.items)} results, web client "
                  f"{yt.client_version}")
    except YouTubeError as e:
        d.say(BAD, "search", str(e))
    try:
        v = video.get(yt, PROBE_VIDEO)
        if not v.info["title"] or not v.related.items:
            d.say(BAD, "video page", "not read whole: YouTube changed, vts "
                  "update")
        else:
            d.say(OK, "video page", f"{len(v.related.items)} videos up next"
                  + (", comments" if v.comments_token else ""))
    except YouTubeError as e:
        d.say(BAD, "video page", str(e))
    try:
        page, name = feed.get(yt, PROBE_CHANNEL)
        d.say(OK, "feeds", f"{len(page.items)} videos from "
              f"{name or PROBE_CHANNEL}")
    except YouTubeError as e:
        d.say(BAD, "feeds", f"{e}: new videos can't be found")
    try:
        d.say(OK, "suggestions", str(len(yt.suggest(PROBE_TYPED))))
    except YouTubeError as e:
        d.say(WARN, "suggestions", str(e))


def doctor_thumbnails(d, cfg):
    if cfg.thumbnails == "no":
        d.say(INFO, "thumbnails", "off in the settings")
        return
    try:
        from vts import thumbs
    except ImportError:
        d.say(INFO, "thumbnails", "not available")
        return
    try:
        t = thumbs.Thumbs(cfg.thumbnails)
    except thumbs.Unavailable as e:
        d.say(INFO, "thumbnails", str(e))
        return
    t0 = time.monotonic()
    got = None
    while time.monotonic() - t0 < PROBE_THUMB_TIME:
        got = t.get(PROBE_VIDEO, 32, 9)
        if got is not None:
            break
        t.poll()
        time.sleep(0.02)
    if got:
        d.say(OK, "thumbnails", f"{t.mode}, for this terminal")
    else:
        d.say(WARN, "thumbnails", f"{t.mode}, but none could be fetched")
    t.close()


def run_doctor(cfg, write=None, network=True):
    d = Doctor(write)
    doctor_local(d, cfg)
    yt = Client(cfg)
    doctor_rules(d, cfg, yt)
    if network:
        doctor_youtube(d, yt)
    doctor_thumbnails(d, cfg)
    return d


def cmd_doctor(cfg, argv):
    if argv:
        if argv[0] in ("--help", "-h"):
            out("Usage: vts doctor\nCheck that vts and what it uses work, "
                "and say what to fix.")
            return EXIT_OK
        sys.stderr.write("Usage: vts doctor\n")
        return EXIT_USAGE
    out(f"vts {__version__}")
    d = run_doctor(cfg, out)
    out(d.summary())
    return EXIT_FAIL if d.problems else EXIT_OK


__all__ = ["YouTubeError"]

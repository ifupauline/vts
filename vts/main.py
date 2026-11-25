# SPDX-License-Identifier: Apache-2.0
"""vts: the options, the settings, then a command or the app."""

import getopt
import os
import sys
import textwrap

from vts import __version__, config, log, platform
from vts.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, Usage


class Origin:
    """where the settings came from, for the commands that report it"""

    def __init__(self):
        self.path = None  # None without a home folder and without -c
        self.found = False


def load_settings(cfg, origin):
    """0, or the exit status to stop with"""
    origin.path = config.default_path(cfg)
    if not origin.path:
        return 0
    try:
        for m in cfg.load(origin.path):
            log.warn(m)
        origin.found = True
    except FileNotFoundError:
        if cfg.config_file:
            log.err(f"{origin.path}: {os.strerror(2)}")
            return EXIT_FAIL
    except config.BadSettings as e:
        origin.found = True
        for m in e.msgs:
            log.err(m)
        return EXIT_FAIL
    except (OSError, ValueError) as e:
        log.err(f"{origin.path}: {getattr(e, 'strerror', None) or e}")
        return EXIT_FAIL
    return 0


CONFIG_USAGE = """\
Usage: vts config [--help | --init | set KEY VALUE]
Show the settings in use and the file they come from.

      --init         write a settings file with every setting explained,
                     if there is none yet
  set KEY VALUE      change one setting in the settings file (VALUE ""
                     for nothing)
  -h, --help         show this help, with what each setting does

The settings file is {path}
(or the one given with vts -c FILE). Its lines are `key = value`, and
# starts a comment.

Settings:
"""


def config_help(origin):
    out = [CONFIG_USAGE.format(path=origin.path or "(none: no home folder)")]
    section = None
    for s in sorted(config.SETTINGS,
                    key=lambda s: config.SECTIONS.index(s.section)):
        if s.section != section:
            section = s.section
            out.append(f"{section}:\n")
        out.append(f"  {s.key}\n")
        for line in textwrap.wrap(s.about, 68):
            out.append(f"      {line}\n")
        out.append(textwrap.fill(f"Values: {s.values}.", 68,
                                 initial_indent="      ",
                                 subsequent_indent="        ") + "\n")
        out.append(f"      Default: {s.default}.\n\n")
    sys.stdout.write("".join(out).rstrip("\n") + "\n")


def cmd_config(cfg, origin, argv):
    if argv[:1] in (["-h"], ["--help"]) and len(argv) == 1:
        config_help(origin)
        return EXIT_OK
    if argv == ["--init"]:
        if not origin.path:
            log.err("no home folder: where would the settings go?")
            return EXIT_FAIL
        if os.path.exists(origin.path):
            log.err(f"{origin.path} already exists: change a setting with "
                    "`vts config set KEY VALUE`, or edit it")
            return EXIT_FAIL
        try:
            d = os.path.dirname(origin.path)
            if d:
                platform.mkdirs(d)
            platform.write_atomic(origin.path, config.init_text(cfg))
        except OSError as e:
            log.err(f"{origin.path}: {e.strerror}")
            return EXIT_FAIL
        print(f"wrote {origin.path}, every setting explained")
        return EXIT_OK
    if argv[:1] == ["set"]:
        if len(argv) != 3:
            print("vts config set: KEY VALUE (vts config --help lists them)",
                  file=sys.stderr)
            return EXIT_USAGE
        key, value = argv[1], argv[2]
        s = config.setting(key)
        if not s:
            print(f"vts config set: no setting {key}: "
                  + ", ".join(x.key for x in config.SETTINGS),
                  file=sys.stderr)
            return EXIT_USAGE
        try:
            cfg.set(key, value)
        except config.BadValue as e:
            print(f"vts config set: {key}: {e}", file=sys.stderr)
            return EXIT_USAGE
        if not origin.path:
            log.err("no home folder: where would the settings go?")
            return EXIT_FAIL
        try:
            config.edit_file(origin.path, key, cfg.get(key))
        except OSError as e:
            log.err(f"{origin.path}: {e.strerror}")
            return EXIT_FAIL
        v = cfg.get(key)
        print(f"{key} = {v}" if v != "" else f"{key} =")
        return EXIT_OK
    if argv:
        print("vts: config takes no argument (but --help, --init or set)",
              file=sys.stderr)
        return EXIT_USAGE
    if not origin.path:
        head = "# no settings file: no home folder\n"
    else:
        head = (f"# settings file: {origin.path}"
                f"{'' if origin.found else ' (not found, defaults used)'}\n")
    sys.stdout.write(head + cfg.write())
    return EXIT_OK


def _commands():
    from vts.cli import browse
    table = {
        "search": browse.cmd_search,
        "suggest": browse.cmd_suggest,
        "info": browse.cmd_info,
        "comments": browse.cmd_comments,
        "channel": browse.cmd_channel,
        "playlist": browse.cmd_playlist,
        "feed": browse.cmd_feed,
    }
    try:
        from vts.cli import library
        table.update(fav=library.cmd_fav, history=library.cmd_history,
                     list=library.cmd_list, later=library.cmd_later)
    except ImportError:
        pass
    try:
        from vts.cli import play
        table.update(play=play.cmd_play, download=play.cmd_download,
                     subtitles=play.cmd_subtitles)
    except ImportError:
        pass
    try:
        from vts.cli import upkeep
        table.update(check=upkeep.cmd_check, update=upkeep.cmd_update,
                     doctor=upkeep.cmd_doctor, notified=upkeep.cmd_notified)
    except ImportError:
        pass
    return table


def _setup_streams():
    # without a console (pythonw, a scheduled task): nowhere to write
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    for s in (sys.stdout, sys.stderr):
        try:
            if platform.WINDOWS:
                s.reconfigure(encoding="utf-8", errors="replace")
            else:
                s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    _setup_streams()
    cfg = config.Config()
    try:
        opts, args = getopt.getopt(argv, "c:vhV",
                                   ["config=", "verbose", "offline", "help",
                                    "version"])
    except getopt.GetoptError as e:
        print(f"vts: {e.msg}", file=sys.stderr)
        print("Try 'vts --help' for more information.", file=sys.stderr)
        return EXIT_USAGE
    for o, v in opts:
        if o in ("-c", "--config"):
            cfg.config_file = v
        elif o in ("-v", "--verbose"):
            cfg.verbose = True
        elif o == "--offline":
            cfg.offline = True
        elif o in ("-h", "--help"):
            sys.stdout.write(config.usage())
            return EXIT_OK
        else:
            print(f"vts {__version__}")
            return EXIT_OK
    if cfg.verbose:
        log.set_level(log.DEBUG)
    origin = Origin()
    status = load_settings(cfg, origin)
    if status:
        return status
    try:
        if not args:
            from vts.tui import app
            return app.run(cfg)
        cmd, rest = args[0], args[1:]
        if cmd == "config":
            return cmd_config(cfg, origin, rest)
        run = _commands().get(cmd)
        if not run:
            print(f"vts: unknown command '{cmd}'", file=sys.stderr)
            print("Try 'vts --help' for more information.", file=sys.stderr)
            return EXIT_USAGE
        return run(cfg, rest)
    except Usage:
        return EXIT_USAGE
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # `vts search ... | head`: the rest isn't wanted
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return EXIT_OK
    finally:
        log.close()

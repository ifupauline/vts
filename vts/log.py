# SPDX-License-Identifier: Apache-2.0
"""Messages: on stderr as "vts: level: ...", or into a file with the time."""

import sys
import time

from vts import text

ERROR, WARN, INFO, DEBUG = range(4)
_NAMES = ("error", "warning", "info", "debug")

_level = WARN
_file = None


def set_level(level):
    global _level
    _level = level


def level():
    return _level


def set_file(path):
    """write the messages into @path (None: back to stderr)"""
    global _file
    close()
    if path:
        from vts import platform
        _file = platform.open_append(path)


def close():
    global _file
    if _file:
        try:
            _file.close()
        except OSError:
            pass
    _file = None


def log(lvl, msg):
    if lvl > _level:
        return
    line = text.clean(str(msg))
    try:
        if _file:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            _file.write(f"{stamp} {_NAMES[lvl]}: {line}\n")
            _file.flush()
        else:
            sys.stderr.write(f"vts: {_NAMES[lvl]}: {line}\n")
            sys.stderr.flush()
    except (OSError, ValueError):
        pass


def err(msg):
    log(ERROR, msg)


def warn(msg):
    log(WARN, msg)


def info(msg):
    log(INFO, msg)


def dbg(msg):
    log(DEBUG, msg)

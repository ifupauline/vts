# SPDX-License-Identifier: Apache-2.0
"""
Desktop notifications: notify-send on Linux (with Play and Watch later
buttons where the desktop has them), Windows' own on Windows.
"""

import subprocess
import sys

from vts import log, platform

ACTION_WAIT = 12 * 3600  # how long a notification's buttons stay useful


def _escaped(s):
    """notify-send takes some markup in the body: & < > must be escaped"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_actions_ok = {}


def _has_actions(path):
    """whether this notify-send has --action (libnotify 0.7.10 and later)"""
    if path not in _actions_ok:
        ok = False
        try:
            p = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=5)
            words = p.stdout.decode(errors="replace").split()
            if len(words) == 2 and words[0] == "notify-send":
                v = tuple(int(x) for x in words[1].split(".")[:3])
                ok = v >= (0, 7, 10)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        _actions_ok[path] = ok
    return _actions_ok[path]


def send(notifier, summary, body, video=None):
    """
    show a notification; with @video (an id), buttons to play it or keep
    it for later, handled by a vts of its own waiting in the background.
    FileNotFoundError when nothing can show it.
    """
    if platform.WINDOWS:
        _toast(summary, body, video)
        return
    path = platform.find_program(notifier)
    if not path:
        raise FileNotFoundError(notifier)
    if video and _has_actions(path):
        # the notification waits for a click: not this process's business
        try:
            platform.spawn([*platform.self_command(), "notified", path,
                            video, summary, body], quiet=True, detach=True)
            return
        except OSError:
            pass
    try:
        subprocess.run([path, "--app-name=vts", "--", summary,
                        _escaped(body)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except subprocess.TimeoutExpired:
        pass


def wait_action(cfg, notifier, video, summary, body):
    """
    `vts notified`: show the notification with its buttons, wait for one
    to be clicked, do what it says
    """
    from vts.rules import valid_video_id
    if not valid_video_id(video):
        return 2
    try:
        p = subprocess.run([notifier, "--app-name=vts", "--wait",
                            "--action=play=Play",
                            "--action=later=Watch later", "--", summary,
                            _escaped(body)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=ACTION_WAIT)
    except (OSError, subprocess.TimeoutExpired):
        return 1
    what = p.stdout.decode(errors="replace").strip()
    if what == "play":
        from vts.cli import play
        return play.cmd_play(cfg, [video])
    if what == "later":
        from vts import video as v
        from vts.library import EXISTS, WATCH_LATER, LibError, Library
        from vts.rules import Item
        from vts.youtube import Client, YouTubeError
        try:
            it = v.get(Client(cfg), video).info
        except YouTubeError:
            it = Item("video", id=video, title=body)
        try:
            with Library() as lib:
                lib.list_add(WATCH_LATER, it)
        except LibError as e:
            if e.kind != EXISTS:
                log.err(str(e))
                return 1
    return 0


def _ps_quote(s):
    return "'" + s.replace("'", "''") + "'"


def _xml(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _toast(summary, body, video):
    """a Windows notification, through PowerShell (nothing to install)"""
    actions = ""
    if video:
        url = f"https://www.youtube.com/watch?v={video}"
        actions = ('<actions><action content="Open on YouTube" '
                   f'activationType="protocol" arguments="{_xml(url)}"/>'
                   "</actions>")
    xml = ('<toast><visual><binding template="ToastGeneric">'
           f"<text>{_xml(summary)}</text><text>{_xml(body)}</text>"
           f"</binding></visual>{actions}</toast>")
    app = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0" \
          r"\powershell.exe"
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$x.LoadXml({_ps_quote(xml)});"
        "$t = New-Object Windows.UI.Notifications.ToastNotification $x;"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier({_ps_quote(app)}).Show($t)")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", script], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, **platform._popen_flags(False))
    except FileNotFoundError:
        raise FileNotFoundError("powershell") from None
    except (OSError, subprocess.TimeoutExpired):
        pass


def available(notifier):
    """what shows notifications, or None"""
    if platform.WINDOWS:
        return platform.find_program("powershell")
    return platform.find_program(notifier)


__all__ = ["send", "wait_action", "available", "sys"]

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Pretends to be mpv for the tests: takes mpv's command line, opens the
JSON IPC socket of --input-ipc-server and "plays" each video given, as
the environment says. Under any name without "mpv" in it (yt-dlp,
notify-send, systemctl), it only writes its command line to the log.

  FAKE_LOG    file where the command line is written (JSON), then each
              command received through the socket
  FAKE_PLAN   [[position, length], ...]: where each video ends up
              (default: each played to its end, 100 s long)
  FAKE_WAIT   1: after the plan, wait to be told to quit
  FAKE_VERSION what --version says (today's date, as yt-dlp's are)
  FAKE_FAIL   1: write a yt-dlp error to --log-file and end, playing nothing

With --progress-template (a download from the app), it tells its
progress like yt-dlp, in FAKE_DL_STEPS steps of 0.4 s (4), or fails when
the file FAKE_DL_FAIL_FILE exists. With --update alone, it says it
updated itself (as yt-dlp does); with
-J, it describes a video with subtitles (none with FAKE_NO_SUBS=1).
"""
import json
import os
import socket
import sys
import time


def log(entry):
    path = os.environ.get("FAKE_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def send(conn, obj):
    try:
        conn.sendall((json.dumps(obj) + "\n").encode())
    except OSError:
        pass


def main():
    args = sys.argv[1:]
    name = os.path.basename(sys.argv[0])
    if args == ["--version"]:
        # not logged: the tests look at what plays and downloads
        version = os.environ.get("FAKE_VERSION",
                                 time.strftime("%Y.%m.%d", time.gmtime()))
        print(f"mpv {version} Copyright" if "mpv" in name else version)
        return 0
    log({"argv": args})
    if "--progress-template" in args:
        # yt-dlp downloading for the app: its progress, one line a step
        fail = os.environ.get("FAKE_DL_FAIL_FILE")
        steps = int(os.environ.get("FAKE_DL_STEPS", "4"))
        print("[youtube] Extracting URL: " + args[-1], flush=True)
        if fail and os.path.exists(fail):
            # as yt-dlp does, after a progress line
            print("ERROR: \r[youtube] dQw4w9WgXcQ: Video unavailable",
                  file=sys.stderr, flush=True)
            return 1
        print("[download] Destination: x.webm", flush=True)
        for i in range(steps + 1):
            print(f"vts-progress {i * 250000} NA {steps * 250000} "
                  f"1250000.5 {steps - i}", flush=True)
            time.sleep(0.4)
        print("[Merger] Merging formats into \"x.mkv\"", flush=True)
        time.sleep(0.4)
        return 0
    if "-J" in args:
        # yt-dlp describing a video: its subtitles are what matter here
        if os.environ.get("FAKE_NO_SUBS"):
            print(json.dumps({"id": args[-1][-11:]}))
            return 0
        print(json.dumps({
            "id": args[-1][-11:],
            "subtitles": {
                "en": [{"ext": "vtt", "name": "English"}],
                "de-DE": [{"ext": "vtt", "name": "German (Germany)"}],
                "live_chat": [{"ext": "json", "name": "Live chat"}],
                "x;rm -rf": [{"ext": "vtt", "name": "Bad"}],
            },
            "automatic_captions": {
                "en-orig": [{"ext": "vtt", "name": "English (Original)"}],
                "en": [{"ext": "vtt", "name": "English"}],
                "fr": [{"ext": "vtt", "name": "French\u001b[31m"}],
                "de": [{"ext": "vtt", "name": "German"}],
            }}))
        return 0
    if args == ["--update"]:
        print("Updated yt-dlp to stable@2026.11.11")
        return 0
    # under another name (yt-dlp, notify-send, systemctl): only the log
    if "mpv" not in os.path.basename(sys.argv[0]):
        return 0
    logfile = next((a.split("=", 1)[1] for a in args
                    if a.startswith("--log-file=")), None)
    if os.environ.get("FAKE_FAIL") and logfile:
        # as when yt-dlp can't get the video
        with open(logfile, "w") as f:
            f.write("[   0.010][v][cplayer] Command line options: ...\n"
                    "[   0.400][e][ytdl_hook] ERROR: [youtube] dQw4w9WgXcQ:"
                    " Sign in to confirm you\u2019re not a bot\n"
                    "[   0.420][e][cplayer] Failed to recognize file "
                    "format.\n")
        return 2
    sock_path = next(a.split("=", 1)[1] for a in args
                     if a.startswith("--input-ipc-server="))
    files = [a for a in args if a.startswith("https://")]
    plan = json.loads(os.environ.get("FAKE_PLAN", "null")) or \
        [[100, 100] for _ in files]

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(10)
    conn, _ = srv.accept()
    conn.settimeout(0.05)
    buf = b""
    observed = set()
    quit_asked = False

    def pump():
        nonlocal buf, quit_asked
        try:
            data = conn.recv(65536)
        except TimeoutError:
            return
        except OSError:
            quit_asked = True
            return
        if not data:
            quit_asked = True
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            cmd = json.loads(line)["command"]
            log({"command": cmd})
            if cmd[0] == "observe_property":
                observed.add(cmd[2])
            elif cmd[0] == "quit":
                quit_asked = True

    # wait for vts to ask for what it follows
    deadline = time.monotonic() + 5
    while len(observed) < 4 and time.monotonic() < deadline:
        pump()
    for i, (pos, length) in enumerate(plan[:len(files)]):
        send(conn, {"event": "property-change", "id": 1,
                    "name": "playlist-pos", "data": i})
        send(conn, {"event": "property-change", "id": 4,
                    "name": "media-title", "data": f"Fake video {i + 1}"})
        send(conn, {"event": "property-change", "id": 3,
                    "name": "duration", "data": length})
        for t in range(0, pos + 1, max(1, pos // 5) if pos else 1):
            send(conn, {"event": "property-change", "id": 2,
                        "name": "time-pos", "data": float(t)})
            pump()
            if quit_asked:
                break
        send(conn, {"event": "property-change", "id": 2,
                    "name": "time-pos", "data": float(pos)})
        if quit_asked:
            break
    if os.environ.get("FAKE_WAIT") == "1":
        deadline = time.monotonic() + 20
        while not quit_asked and time.monotonic() < deadline:
            pump()
    time.sleep(0.1)
    conn.close()
    srv.close()
    os.unlink(sock_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

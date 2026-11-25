#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Runs the vts program and checks what it prints and how it exits.

usage: test_cli.py VTS VERSION
"""
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

failures = 0
checks = 0


def check(cond, what):
    global failures, checks
    checks += 1
    if not cond:
        failures += 1
        print(f"CHECK failed: {what}", file=sys.stderr)
    return cond


def run(vts, *args, env=None):
    p = subprocess.run([vts, *args], capture_output=True, text=True,
                       timeout=10, env=env)
    return p.returncode, p.stdout, p.stderr


def test_version(vts, version):
    for opt in ("-V", "--version"):
        code, out, err = run(vts, opt)
        check(code == 0, f"{opt}: exit status {code}")
        check(out == f"vts {version}\n", f"{opt}: printed {out!r}")
        check(err == "", f"{opt}: stderr {err!r}")


def test_help(vts, version):
    for opt in ("-h", "--help"):
        code, out, err = run(vts, opt)
        check(code == 0, f"{opt}: exit status {code}")
        check(out.startswith("Usage: vts "), f"{opt}: printed {out!r}")
        check("--version" in out, f"{opt}: --version not listed")
        check(err == "", f"{opt}: stderr {err!r}")


def test_bad_usage(vts, version):
    for args in (["--nope"], ["-x"], ["nosuchcommand"], ["--version=1"],
                 ["-c"]):
        code, out, err = run(vts, *args)
        check(code == 2, f"{args}: exit status {code}, expected 2")
        check(out == "", f"{args}: stdout {out!r}")
        check("vts --help" in err, f"{args}: stderr {err!r}")


def test_config(vts, work):
    home = os.path.join(work, "home")
    conf = os.path.join(home, ".config", "vts")
    os.makedirs(conf)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
           "LANG": "fr_FR.UTF-8"}

    # no settings file: the defaults, language and region from the locale
    code, out, err = run(vts, "config", env=env)
    check(code == 0, f"config without a file: exit status {code}, {err!r}")
    check(f"# settings file: {conf}/config (not found, defaults used)\n"
          in out, f"config without a file: {out!r}")
    check("language = fr\nregion = FR\nquality = 1080\n" in out,
          f"config defaults: {out!r}")

    # the usual file, and an unknown setting only warned about
    with open(os.path.join(conf, "config"), "w") as f:
        f.write("quality = 720\nshiny\x1b[31m = yes\nregion =\n")
    code, out, err = run(vts, "config", env=env)
    check(code == 0, f"config with a file: exit status {code}, {err!r}")
    check("quality = 720\n" in out and "region =\n" in out,
          f"config with a file: {out!r}")
    check("unknown setting 'shiny[31m': ignored" in err,
          f"unknown setting: {err!r}")
    check("\x1b" not in err, f"escape sequence in the warning: {err!r}")

    # the output is a settings file that gives the same output
    copy = os.path.join(work, "copy")
    with open(copy, "w") as f:
        f.write(out)
    code, again, err = run(vts, "-c", copy, "config", env=env)
    check(code == 0 and again.split("\n", 1)[1] == out.split("\n", 1)[1],
          f"config read back: {again!r}")

    # a wrong value stops vts, with the file and the line
    bad = os.path.join(work, "bad")
    with open(bad, "w") as f:
        f.write("# comment\nquality = 1000\n")
    code, out, err = run(vts, "--config", bad, "config", env=env)
    check(code == 1, f"bad settings: exit status {code}")
    check(f"{bad}:2: bad value for 'quality'" in err,
          f"bad settings: {err!r}")
    check(out == "", f"bad settings: stdout {out!r}")

    code, out, err = run(vts, "-c", os.path.join(work, "missing"), "config",
                         env=env)
    check(code == 1 and "No such file" in err, f"missing -c file: {err!r}")

    code, out, err = run(vts, "config", "extra", env=env)
    check(code == 2, f"config with an argument: exit status {code}")

    # without a home folder, the defaults
    env.pop("HOME")
    code, out, err = run(vts, "config", env=env)
    check(code == 0 and out.startswith("# no settings file"),
          f"config without HOME: {code} {out!r} {err!r}")


class FakeYouTube:
    """fake_server.py with the recorded YouTube answers"""

    def __init__(self, work):
        portfile = os.path.join(work, "port")
        if os.path.exists(portfile):
            os.unlink(portfile)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "fake_server.py"), portfile,
             os.path.join(HERE, "fixtures", "youtube")])
        for _ in range(200):
            if os.path.exists(portfile):
                break
            time.sleep(0.05)
        with open(portfile) as f:
            self.url = "http://127.0.0.1:" + f.read().strip()

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as r:
            return r.read().decode()

    def last_request(self):
        return json.loads(self.get("/last-request"))

    def stop(self):
        self.proc.terminate()
        self.proc.wait()


def run_tty(vts, *args, env=None, cols=100):
    """run vts on a pseudo terminal @cols wide, return what it printed"""
    import fcntl
    import struct
    import termios
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(vts, [vts, *args], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, cols, 0, 0))
    out = b""
    while True:
        r, _, _ = select.select([fd], [], [], 10)
        if not r:
            break
        try:
            data = os.read(fd, 65536)
        except OSError:
            break
        if not data:
            break
        out += data
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


def test_search(vts, work):
    yt = FakeYouTube(work)
    cache = os.path.join(work, "cache")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home2"), "LANG": "en_US.UTF-8",
           "XDG_CACHE_HOME": cache, "VTS_BASE_URL": yt.url}
    try:
        # one line per result: kind, id, title, channel, length, views, date
        code, out, err = run(vts, "search", "linus", "torvalds", env=env)
        check(code == 0, f"search: exit status {code}, {err!r}")
        lines = out.splitlines()
        check(len(lines) >= 15, f"search: {len(lines)} results")
        check(lines and lines[0].split("\t") == [
            "video", "mfv0V1SxbNA",
            "Building the PERFECT Linux PC with Linus Torvalds",
            "Linus Tech Tips", "53:44", "9,590,258 views", "11 months ago"],
            f"search: first line {lines[:1]!r}")
        check(all(len(l.split("\t")) == 7 for l in lines),
              "search: every line has 7 fields")
        check(any(l.startswith("short\t") for l in lines), "search: Shorts")

        # what was asked: the web client read from the home page, the
        # language and the region of the settings
        req = yt.last_request()
        client = req["context"]["client"]
        check(client["clientVersion"] == "2.20991231.00.00",
              f"search: client version {client!r}")
        check(client["hl"] == "en" and client["gl"] == "US",
              f"search: language {client!r}")
        check(req["query"] == "linus torvalds" and "params" not in req,
              f"search: request {req!r}")
        with open(os.path.join(cache, "vts", "client")) as f:
            check(f.read().startswith(
                "2.20991231.00.00 AIzaFakeKeyForTheTestServer_0123 "),
                "search: client version kept in the cache")

        # filters, as the website sends them
        code, out, err = run(vts, "search", "-s", "views", "--type=video",
                             "-d", "week", "-l", "long", "-f", "4k",
                             "--feature", "subtitles", "linus torvalds",
                             env=env)
        req = yt.last_request()
        check(req.get("params") == "CAMSCggDEAEYAigBcAE=",
              f"search filters: params {req.get('params')!r}")

        # more pages
        code, out2, err = run(vts, "search", "-p", "2", "linus torvalds",
                              env=env)
        check(code == 0 and len(out2.splitlines()) >= len(lines) + 10,
              f"search -p 2: {len(out2.splitlines())} lines, {err!r}")

        # JSON
        code, out, err = run(vts, "search", "--json", "-t", "channel",
                             "kurzgesagt", env=env)
        items = json.loads(out) if code == 0 else []
        check(items and items[0] == {
            "kind": "channel", "id": "UCsXVk37bltHxD1rDPwtNM8Q",
            "title": "Kurzgesagt \u2013 In a Nutshell",
            "handle": "@kurzgesagt", "subscribers": "25.8M subscribers",
            "description": items[0].get("description"),
            "thumbnail": items[0].get("thumbnail")},
            f"search --json: {items[:1]!r}")
        code, out, err = run(vts, "search", "-j", "-f", "live", "lofi radio",
                             env=env)
        items = json.loads(out) if code == 0 else []
        check(items and all(i.get("live") is True for i in items),
              "search --json: live streams marked live")

        # another language
        env_fr = dict(env, LANG="fr_FR.UTF-8")
        code, out, err = run(vts, "search", "linus torvalds", env=env_fr)
        check(code == 0 and "vues" in out, f"search in French: {err!r}")
        check(yt.last_request()["context"]["client"]["gl"] == "FR",
              "search in French: region FR")

        # on a terminal: the title, then the details, cut to the width
        code, out = run_tty(vts, "search", "linus torvalds", env=env,
                            cols=40)
        tl = out.split("\r\n")
        check(code == 0 and tl[0] == "Building the PERFECT Linux PC with Linu\u2026"
              and tl[1].startswith("  video mfv0V1SxbNA \u00b7 53:44"),
              f"search on a terminal: {tl[:2]!r}")
        check(all(len(l) <= 40 for l in tl), "search on a terminal: width")

        # failures, said plainly
        start = time.monotonic()
        code, out, err = run(vts, "search", "too many requests", env=env)
        check(code == 1 and "automated traffic check" in err and out == "",
              f"bot check: {code} {err!r}")
        check(time.monotonic() - start >= 1.4, "bot check: tried again")
        code, out, err = run(vts, "search", "refused", env=env)
        check(code == 1 and "invalid argument" in err,
              f"refused: {code} {err!r}")
        code, out, err = run(vts, "search", "not json", env=env)
        check(code == 1 and "not JSON" in err, f"not json: {code} {err!r}")
        bad = dict(env, VTS_BASE_URL="http://127.0.0.1:1")
        code, out, err = run(vts, "search", "linus", env=bad)
        check(code == 1 and "can't reach YouTube" in err,
              f"no server: {code} {err!r}")

        for args in (["search"], ["search", "-t", "song", "x"],
                     ["search", "-f", "live,smell", "x"],
                     ["search", "-p", "0", "x"], ["suggest"]):
            code, out, err = run(vts, *args, env=env)
            check(code == 2 and err, f"{args}: exit status {code}, {err!r}")
        code, out, err = run(vts, "search", "--help", env=env)
        check(code == 0 and "--duration" in out, "search --help")

        # the video page
        code, out, err = run(vts, "info", "https://youtu.be/mfv0V1SxbNA?t=4",
                             env=env)
        lines = out.splitlines()
        check(code == 0 and lines[:4] == [
            "Building the PERFECT Linux PC with Linus Torvalds",
            "Linus Tech Tips \u00b7 @LinusTechTips \u00b7 16.9M subscribers",
            lines[2] if len(lines) > 2 else "",
            "https://www.youtube.com/watch?v=mfv0V1SxbNA"],
            f"info: {lines[:4]!r} {err!r}")
        check(len(lines) > 2 and "Nov 30, 2025" in lines[2] and
              lines[2].endswith(" likes"), f"info: {lines[2:3]!r}")
        check("Chapters:" in lines and "      2:42  Autobiography" in lines,
              "info: chapters")
        check("Up next:" in lines and
              sum(1 for l in lines[lines.index("Up next:"):]
                  if l.startswith("  ")) == 5 if "Up next:" in lines
              else False, "info: five videos up next")
        req = yt.last_request()
        check(req.get("videoId") == "mfv0V1SxbNA", f"info: request {req!r}")

        code, out, err = run(vts, "info", "--json", "dQw4w9WgXcQ", env=env)
        v = json.loads(out) if code == 0 else {}
        check(v.get("id") == "dQw4w9WgXcQ" and v.get("chapters") == [] and
              len(v.get("related", [])) >= 10 and v.get("comments") is True,
              f"info --json: {str(v)[:200]}")

        code, out, err = run(vts, "info", "notavideo", env=env)
        check(code == 2 and "not a video" in err, f"info bad id: {err!r}")
        code, out, err = run(vts, "info", "aaaaaaaaaaa", env=env)
        check(code == 1 and "no fixture" in err, f"info unknown: {err!r}")

        # comments, top ones first, the pinned one first of all
        code, out, err = run(vts, "comments", "mfv0V1SxbNA", env=env)
        lines = out.splitlines()
        check(code == 0 and lines[0].endswith(" Comments") and
              lines[2].startswith("pinned \u00b7 @LinusTechTips \u00b7 creator"),
              f"comments: {lines[:3]!r} {err!r}")
        check(lines[3] == "  Minor Spelling Correction & Clarification:",
              f"comments: text {lines[3:4]!r}")
        heads = [l for l in lines if l and not l.startswith(" ")]
        check(len(heads) == 21, f"comments: {len(heads) - 1} comments")
        code, out, err = run(vts, "comments", "-p", "2", "mfv0V1SxbNA",
                             env=env)
        heads = [l for l in out.splitlines() if l and not l.startswith(" ")]
        check(code == 0 and len(heads) == 41, f"comments -p 2: {len(heads)}")
        code, out, err = run(vts, "comments", "--newest", "mfv0V1SxbNA",
                             env=env)
        check(code == 0 and out.count("\n\n") >= 10,
              f"comments --newest: {code} {err!r}")
        code, out, err = run(vts, "comments", "-j", "-r", "mfv0V1SxbNA",
                             env=env)
        c = json.loads(out) if out else []
        check(len(c) == 20 and c[0].get("pinned") is True and
              len(c[0].get("replies_list", [])) >= 5 and
              c[0]["replies_list"][0]["kind"] == "comment",
              f"comments --json --replies: {str(c)[:200]}")

        # channels: by handle (asked to YouTube), by id, by link
        code, out, err = run(vts, "channel", "@kurzgesagt", env=env)
        lines = out.splitlines()
        check(code == 0 and len(lines) >= 20 and
              all(l.startswith("video\t") for l in lines),
              f"channel: {code} {lines[:1]!r} {err!r}")
        f = lines[0].split("\t") if lines else []
        check(len(f) == 7 and f[3] == "" and f[5].endswith(" views"),
              f"channel: fields {f!r}")
        req = yt.last_request()
        check(req.get("browseId") == "UCsXVk37bltHxD1rDPwtNM8Q" and
              req.get("params") == "EgZ2aWRlb3PyBgQKAjoA",
              f"channel: request {req!r}")
        code, out2, err = run(
            vts, "channel", "-p", "2",
            "https://www.youtube.com/channel/UCsXVk37bltHxD1rDPwtNM8Q/about",
            env=env)
        check(code == 0 and len(out2.splitlines()) >= len(lines) + 20,
              f"channel -p 2: {len(out2.splitlines())} {err!r}")
        code, out, err = run(vts, "channel", "-t", "shorts",
                             "UCsXVk37bltHxD1rDPwtNM8Q", env=env)
        check(code == 0 and out.startswith("short\t"), f"channel shorts {err!r}")
        code, out, err = run(vts, "channel", "--tab=playlists",
                             "UCsXVk37bltHxD1rDPwtNM8Q", env=env)
        check(code == 0 and out.startswith("playlist\tPL"),
              f"channel playlists {err!r}")
        code, out, err = run(vts, "channel", "-s", "black hole",
                             "UCsXVk37bltHxD1rDPwtNM8Q", env=env)
        check(code == 0 and "Black Hole" in out and
              yt.last_request().get("query") == "black hole",
              f"channel search {err!r}")
        code, out, err = run(vts, "channel", "-j", "UCsXVk37bltHxD1rDPwtNM8Q",
                             env=env)
        c = json.loads(out) if code == 0 else {}
        check(c.get("handle") == "@kurzgesagt" and
              c.get("subscribers", "").endswith("subscribers") and
              len(c.get("items", [])) >= 20, f"channel --json {str(c)[:200]}")
        for args in (["channel"], ["channel", "-t", "posts", "@x"],
                     ["channel", "a b"]):
            code, out, err = run(vts, *args, env=env)
            check(code in (1, 2) and err, f"{args}: {code} {err!r}")
        code, out, err = run(vts, "channel", "@nobody", env=env)
        check(code == 1 and "no channel @nobody" in err,
              f"unknown handle: {err!r}")

        # a playlist, from a link that plays it
        code, out, err = run(
            vts, "playlist",
            "https://www.youtube.com/watch?v=4_aOIA-vyBo"
            "&list=PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs", env=env)
        lines = out.splitlines()
        check(code == 0 and len(lines) == 77 and
              lines[0].startswith("video\t4_aOIA-vyBo\t"),
              f"playlist: {len(lines)} {lines[:1]!r} {err!r}")
        code, out, err = run(vts, "playlist", "-j",
                             "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs", env=env)
        pl = json.loads(out) if code == 0 else {}
        check(pl.get("title") == "The Existential Crisis Playlist" and
              pl.get("videos") == "77 videos", f"playlist --json {str(pl)[:200]}")
        code, out, err = run(vts, "playlist", "x;y", env=env)
        check(code == 2, f"playlist bad id: {code}")

        # a channel's feed, with and without Shorts
        code, out, err = run(vts, "feed", "@kurzgesagt", env=env)
        lines = out.splitlines()
        check(code == 0 and len(lines) == 15 and
              any(l.startswith("short\t") for l in lines) and
              all(l.split("\t")[6].startswith("20") for l in lines),
              f"feed: {code} {lines[:1]!r} {err!r}")
        code, out, err = run(vts, "feed", "--no-shorts",
                             "UCsXVk37bltHxD1rDPwtNM8Q", env=env)
        lines = out.splitlines()
        check(code == 0 and len(lines) == 15 and
              all(l.startswith("video\t") for l in lines),
              f"feed --no-shorts: {code} {err!r}")
        code, out, err = run(vts, "feed", "UCaaaaaaaaaaaaaaaaaaaaaa", env=env)
        check(code == 1 and "HTTP 404" in err, f"feed missing: {err!r}")

        # favourites: added by handle, their name and handle from YouTube
        env_lib = dict(env, XDG_DATA_HOME=os.path.join(work, "data"))
        code, out, err = run(vts, "fav", "add", "-g", "Science",
                             "@kurzgesagt", env=env_lib)
        check(code == 0 and out == "added Kurzgesagt \u2013 In a Nutshell\n",
              f"fav add: {code} {out!r} {err!r}")
        db = os.path.join(work, "data", "vts", "vts.db")
        check(os.path.exists(db) and os.stat(db).st_mode & 0o777 == 0o600,
              "fav add: a private library")
        code, out, err = run(vts, "fav", "add", "UCsXVk37bltHxD1rDPwtNM8Q",
                             env=env_lib)
        check(code == 0 and "already a favourite" in out, f"fav add again {out!r}")
        code, out, err = run(vts, "fav", env=env_lib)
        check(out == "UCsXVk37bltHxD1rDPwtNM8Q\tKurzgesagt \u2013 In a "
              "Nutshell\t@kurzgesagt\tScience\tyes\tyes\n",
              f"fav: {out!r} {err!r}")
        code, out, err = run(vts, "fav", "set", "@kurzgesagt", "shorts=no",
                             "group=", env=env_lib)
        code, out, err = run(vts, "fav", "--json", env=env_lib)
        favs = json.loads(out) if code == 0 else []
        check(favs and favs[0]["shorts"] is False and favs[0]["group"] == "",
              f"fav set: {favs!r}")
        code, out, err = run(vts, "fav", "set", "@kurzgesagt", "volume=11",
                             env=env_lib)
        check(code == 1 and "expected notify" in err, f"fav set bad {err!r}")

        # export, import elsewhere
        code, opml, err = run(vts, "fav", "export", env=env_lib)
        check(code == 0 and "channel_id=UCsXVk37bltHxD1rDPwtNM8Q" in opml,
              f"fav export: {opml!r}")
        path = os.path.join(work, "subs.opml")
        with open(path, "w") as f:
            f.write(opml)
        env_lib2 = dict(env, XDG_DATA_HOME=os.path.join(work, "data2"))
        code, out, err = run(vts, "fav", "import", path, env=env_lib2)
        check(code == 0 and out == "1 channel added\n", f"fav import {out!r}")
        code, out, err = run(vts, "fav", "import", "/etc/hostname",
                             env=env_lib2)
        check(code == 1 and "not a subscriptions file" in err,
              f"fav import bad: {err!r}")
        code, out, err = run(vts, "fav", "remove", "@kurzgesagt",
                             env=env_lib2)
        code, out, err = run(vts, "fav", env=env_lib2)
        check(code == 0 and out == "", f"fav remove: {out!r} {err!r}")
        for args in (["fav", "nope"], ["fav", "remove"], ["fav", "add"]):
            code, out, err = run(vts, *args, env=env_lib)
            check(code == 2, f"{args}: {code}")

        # Watch later and playlists, with titles from YouTube
        code, out, err = run(vts, "later", "add", "https://youtu.be/dQw4w9WgXcQ",
                             env=env_lib)
        check(code == 0, f"later add: {err!r}")
        code, out, err = run(vts, "later", env=env_lib)
        check(out.startswith("video\tdQw4w9WgXcQ\tRick Astley - Never Gonna"),
              f"later: {out!r}")
        code, out, err = run(vts, "later", "add", "dQw4w9WgXcQ", env=env_lib)
        check(code == 0 and "already in" in out, f"later add again: {out!r}")
        code, out, err = run(vts, "list", "create", "Talks", env=env_lib)
        code, out, err = run(vts, "list", "add", "Talks", "mfv0V1SxbNA",
                             "dQw4w9WgXcQ", env=env_lib)
        code, out, err = run(vts, "list", env=env_lib)
        check(out == "Watch later\t1\nTalks\t2\n", f"list: {out!r}")
        code, out, err = run(vts, "list", "show", "Talks", "--json",
                             env=env_lib)
        items = json.loads(out) if code == 0 else []
        check([i["id"] for i in items] == ["mfv0V1SxbNA", "dQw4w9WgXcQ"],
              f"list show: {items!r}")
        code, out, err = run(vts, "list", "delete", "Watch later",
                             env=env_lib)
        check(code == 1 and "can't be deleted" in err, f"list delete WL {err!r}")
        code, out, err = run(vts, "later", "remove", "dQw4w9WgXcQ",
                             env=env_lib)
        code, out, err = run(vts, "later", env=env_lib)
        check(code == 0 and out == "", f"later remove: {out!r}")
        code, out, err = run(vts, "history", env=env_lib)
        check(code == 0 and out == "", f"history: {out!r} {err!r}")

        # suggestions, cleaned of escape sequences
        code, out, err = run(vts, "suggest", "linus", "tor", env=env)
        check(code == 0 and out.splitlines() == [
            "linus torvalds", "linus torvalds interview",
            "linus torvalds [31mred", "linus torvalds nvidia"],
            f"suggest: {out!r} {err!r}")
    finally:
        yt.stop()


def test_play(vts, work):
    yt = FakeYouTube(work)
    fake = os.path.join(HERE, "fake_mpv.py")
    ytdlp = os.path.join(work, "fake_ytdlp")
    os.symlink(fake, ytdlp)
    conf = os.path.join(work, "play.conf")
    with open(conf, "w") as f:
        f.write(f"player = {fake}\ndownloader = {ytdlp}\nquality = 720\n")
    logf = os.path.join(work, "mpv.log")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home3"), "LANG": "en_US.UTF-8",
           "XDG_DATA_HOME": os.path.join(work, "pdata"),
           "XDG_RUNTIME_DIR": os.path.join(work, "run"),
           "XDG_CACHE_HOME": os.path.join(work, "pcache"),
           "VTS_BASE_URL": yt.url, "FAKE_LOG": logf}

    def mpv_log():
        """what mpv was given and told, without the log and yt-dlp's path"""
        with open(logf) as f:
            entries = [json.loads(l) for l in f]
        os.unlink(logf)
        argv = entries[0]["argv"]
        if not any(a.startswith("--input-ipc-server=") for a in argv):
            return entries  # yt-dlp's, for a download
        check(any(a.startswith("--log-file=") and a.endswith("/mpv.log")
                  for a in argv) and
              "--script-opts=ytdl_hook-ytdl_path=" + ytdlp in argv,
              f"play: mpv's log and yt-dlp {argv!r}")
        entries[0]["argv"] = [a for a in argv
                              if not a.startswith(("--log-file=",
                                                   "--script-opts="))]
        return entries

    try:
        # two videos: the first stopped at 2:00, the second watched
        e = dict(env, FAKE_PLAN="[[120, 600], [590, 600]]")
        code, out, err = run(vts, "-c", conf, "play", "dQw4w9WgXcQ",
                             "https://youtu.be/mfv0V1SxbNA?t=1", env=e)
        check(code == 0, f"play: {code} {err!r}")
        argv = mpv_log()[0]["argv"]
        check(argv[1:] == [
            "--ytdl-format=bestvideo[height<=?720]+bestaudio/best[height<=?720]/best",
            "--force-window=immediate",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=mfv0V1SxbNA"]
            and argv[0].startswith("--input-ipc-server="),
            f"play: mpv's arguments {argv!r}")
        code, out, err = run(vts, "history", env=env)
        check(out == "mfv0V1SxbNA\tFake video 2\t\twatched\n"
              "dQw4w9WgXcQ\tFake video 1\t\t2:00\n",
              f"play: history {out!r} {err!r}")

        # again: the first from 2:00, the second from the start
        e = dict(env, FAKE_PLAN="[[200, 600]]")
        code, out, err = run(vts, "-c", conf, "play", "-a", "-s", "1.5",
                             "--subs", "fr,en", "dQw4w9WgXcQ", "mfv0V1SxbNA",
                             env=e)
        argv = mpv_log()[0]["argv"]
        check(code == 0 and argv[1:] == [
            "--ytdl-format=bestaudio/best", "--no-video", "--speed=1.50",
            "--slang=fr,en", "--ytdl-raw-options-append=sub-langs=fr,en",
            "--ytdl-raw-options-append=write-subs=",
            "--ytdl-raw-options-append=write-auto-subs=",
            "--{", "--start=120",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--}",
            "https://www.youtube.com/watch?v=mfv0V1SxbNA"],
            f"play again: {argv!r} {err!r}")
        code, out, err = run(vts, "-c", conf, "play", "--from", "1:02:03",
                             "-n", "dQw4w9WgXcQ", "mfv0V1SxbNA", env=e)
        argv = mpv_log()[0]["argv"]
        check("--start=3723" in argv and argv.count("--{") == 1,
              f"play --from: {argv!r}")

        # a playlist: all its videos
        code, out, err = run(vts, "-c", conf, "play", "-t",
                             "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs", env=e)
        argv = mpv_log()[0]["argv"] if os.path.exists(logf) else [err]
        check(code == 0 and "--vo=tct" in argv and
              sum(a.startswith("https://") for a in argv) == 77,
              f"play a playlist: {code} {err!r}")

        # Ctrl-C: mpv is asked to quit, the position is kept
        e = dict(env, FAKE_PLAN="[[300, 600]]", FAKE_WAIT="1")
        proc = subprocess.Popen([vts, "-c", conf, "play", "-n", "aaaaaaaaaaa"],
                                env=e, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        time.sleep(1.5)
        proc.send_signal(2)
        out, err = proc.communicate(timeout=10)
        cmds = [x.get("command") for x in mpv_log()]
        check(["quit"] in cmds, f"Ctrl-C: mpv told to quit {cmds!r}")
        code, out, err = run(vts, "history", "-n", "1", env=env)
        check(out == "aaaaaaaaaaa\tFake video 1\t\t5:00\n",
              f"Ctrl-C: position kept {out!r}")

        # mpv plays nothing (yt-dlp refused): why, from mpv's log
        e = dict(env, FAKE_FAIL="1")
        code, out, err = run(vts, "-c", conf, "play", "dQw4w9WgXcQ", env=e)
        mpv_log()
        check(code == 1 and "mpv played nothing: ERROR: [youtube] "
              "dQw4w9WgXcQ: Sign in to confirm you\u2019re not a bot" in err,
              f"nothing played: {code} {err!r}")
        with open(os.path.join(work, "noytdlp.conf"), "w") as f:
            f.write(f"player = {fake}\ndownloader = vts-no-such-ytdlp\n")
        code, out, err = run(vts, "-c", os.path.join(work, "noytdlp.conf"),
                             "play", "dQw4w9WgXcQ", env=env)
        check(code == 1 and "vts-no-such-ytdlp is not installed: mpv needs "
              "it" in err and not os.path.exists(logf),
              f"no yt-dlp: {err!r}")

        # without mpv; bad arguments
        with open(os.path.join(work, "nompv.conf"), "w") as f:
            f.write("player = vts-no-such-player\n")
        code, out, err = run(vts, "-c", os.path.join(work, "nompv.conf"),
                             "play", "dQw4w9WgXcQ", env=env)
        check(code == 1 and "vts-no-such-player is not installed" in err,
              f"no player: {err!r}")
        for args in (["play"], ["play", "-q", "999", "x"],
                     ["play", "--from", "1:x", "dQw4w9WgXcQ"],
                     ["play", "-s", "9", "dQw4w9WgXcQ"]):
            code, out, err = run(vts, "-c", conf, *args, env=env)
            check(code == 2, f"{args}: {code}")
        code, out, err = run(vts, "-c", conf, "play", "not/a/video", env=env)
        check(code == 1 and "not a video" in err, f"play bad: {err!r}")

        # downloads: yt-dlp gets the format, where to write, then links
        code, out, err = run(vts, "-c", conf, "download", "-d", "/tmp/x y",
                             "dQw4w9WgXcQ", env=env)
        argv = mpv_log()[0]["argv"]
        check(code == 0 and argv == [
            "--no-playlist", "-f",
            "bestvideo[height<=?720]+bestaudio/best[height<=?720]/best",
            "-o", "/tmp/x y/%(title)s [%(id)s].%(ext)s", "--",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
            f"download: {argv!r} {err!r}")
        code, out, err = run(vts, "-c", conf, "download", "-a",
                             "mfv0V1SxbNA", env=env)
        argv = mpv_log()[0]["argv"]
        check("--extract-audio" in argv and "bestaudio/best" in argv and
              "--audio-format" not in argv, f"download -a: {argv!r}")
        code, out, err = run(vts, "-c", conf, "download", "-q", "480",
                             "--audio-format", "mp3", "mfv0V1SxbNA",
                             env=env)
        argv = mpv_log()[0]["argv"]
        check(code == 0 and argv[:9] == [
            "--no-playlist", "-f", "bestaudio/best", "-o",
            "./%(title)s [%(id)s].%(ext)s", "--extract-audio",
            "--audio-format", "mp3", "--audio-quality"],
            f"download --audio-format: {argv!r}")
        # the sound quality: what is taken from YouTube, the MP3's bitrate
        code, out, err = run(vts, "-c", conf, "download", "--audio-format",
                             "mp3", "--sound-quality", "medium", "mfv0V1SxbNA",
                             env=env)
        argv = mpv_log()[0]["argv"]
        check(code == 0 and argv[2] == "bestaudio/best" and
              argv[argv.index("-S") + 1] == "abr~128" and
              argv[argv.index("--audio-quality") + 1] == "128K",
              f"download --sound-quality: {argv!r}")
        code, out, err = run(vts, "-c", conf, "download", "--sound-quality",
                             "loud", "mfv0V1SxbNA", env=env)
        check(code == 2 and "high, medium or low" in err,
              f"download --sound-quality loud: {code} {err!r}")
        code, out, err = run(vts, "-c", conf, "play", "--sound-quality",
                             "low", "mfv0V1SxbNA", env=env)
        argv = mpv_log()[0]["argv"]
        check(code == 0 and "--ytdl-format=bestaudio/best" in argv and
              "--ytdl-raw-options-append=format-sort=abr~64" in argv and
              "--no-video" in argv,
              f"play --sound-quality: {argv!r} {err!r}")
        code, out, err = run(vts, "-c", conf, "download", "-q", "144",
                             "mfv0V1SxbNA", env=env)
        argv = mpv_log()[0]["argv"]
        check("bestvideo[height<=?144]+bestaudio/best[height<=?144]/best"
              in argv, f"download -q 144: {argv!r}")
        # the downloads setting: where they go without -d
        dconf = os.path.join(work, "dl.conf")
        with open(dconf, "w") as f:
            f.write(f"downloader = {ytdlp}\ndownloads = ~/Videos/vts/\n")
        code, out, err = run(vts, "-c", dconf, "download", "dQw4w9WgXcQ",
                             env=env)
        argv = mpv_log()[0]["argv"]
        where = os.path.join(env["HOME"], "Videos", "vts")
        check(code == 0 and where + "/%(title)s [%(id)s].%(ext)s" in argv and
              os.path.isdir(where), f"downloads = ~/...: {argv!r} {err!r}")
        code, out, err = run(vts, "-c", dconf, "download", "-d", "/tmp/x y",
                             "dQw4w9WgXcQ", env=env)
        argv = mpv_log()[0]["argv"]
        check("/tmp/x y/%(title)s [%(id)s].%(ext)s" in argv,
              f"-d wins: {argv!r}")
        code, out, err = run(vts, "-c", dconf, "config", env=env)
        check("downloads = ~/Videos/vts/\n" in out, f"config: {out!r}")
        with open(dconf, "w") as f:
            f.write("downloads = Videos\n")
        code, out, err = run(vts, "-c", dconf, "config", env=env)
        check(code == 1 and "expected a folder like ~/Videos" in err,
              f"downloads = Videos: {code} {err!r}")

        # the subtitles a video has
        code, out, err = run(vts, "-c", conf, "subtitles",
                             "https://youtu.be/dQw4w9WgXcQ", env=env)
        check(code == 0 and out == "en\tEnglish\nde-DE\tGerman (Germany)\n"
              "en-orig\tEnglish (auto-generated)\n", f"subtitles: {out!r}")
        argv = mpv_log()[0]["argv"]
        check(argv == ["-J", "--skip-download", "--no-playlist",
                       "--no-warnings", "--",
                       "https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
              f"subtitles: yt-dlp told {argv!r}")
        code, out, err = run(vts, "-c", conf, "subtitles", "dQw4w9WgXcQ",
                             env=dict(env, FAKE_NO_SUBS="1"))
        mpv_log()
        check(code == 0 and out == "no subtitles\n", f"no subtitles: {out!r}")
        code, out, err = run(vts, "-c", conf, "subtitles", "not/a/video",
                             env=env)
        check(code == 1 and "not a video" in err, f"subtitles bad: {err!r}")
        for bad in ("ogg", "-o", "mp3 "):
            code, out, err = run(vts, "-c", conf, "download",
                                 "--audio-format", bad, "mfv0V1SxbNA",
                                 env=env)
            check(code == 2 and "mp3, m4a" in err,
                  f"--audio-format {bad!r}: {code} {err!r}")
    finally:
        yt.stop()


def test_check(vts, work):
    yt = FakeYouTube(work)
    fake = os.path.join(HERE, "fake_mpv.py")
    bindir = os.path.join(work, "bin")
    os.makedirs(bindir)
    for name in ("notify-send", "systemctl"):
        os.symlink(fake, os.path.join(bindir, name))
    logf = os.path.join(work, "check.log")
    env = {"PATH": bindir + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home4"), "LANG": "en_US.UTF-8",
           "XDG_DATA_HOME": os.path.join(work, "cdata"),
           "XDG_CONFIG_HOME": os.path.join(work, "cconfig"),
           "XDG_CACHE_HOME": os.path.join(work, "ccache"),
           "VTS_BASE_URL": yt.url, "FAKE_LOG": logf}

    def calls():
        if not os.path.exists(logf):
            return []
        with open(logf) as f:
            entries = [json.loads(l)["argv"] for l in f]
        os.unlink(logf)
        return entries

    try:
        code, out, err = run(vts, "check", env=env)
        check(code == 0 and out == "", f"check without favourites: {err!r}")
        run(vts, "fav", "add", "@kurzgesagt", env=env)

        # the first time: noted, nothing new, no notification
        code, out, err = run(vts, "check", env=env)
        check(code == 0 and out == "" and "first time" in err and
              calls() == [], f"first check: {code} {out!r} {err!r}")

        # two new uploads: printed, and notified
        yt.get("/set-feed?id=UCsXVk37bltHxD1rDPwtNM8Q"
               "&file=feed-UCsXVk37bltHxD1rDPwtNM8Q-new.xml")
        code, out, err = run(vts, "check", env=env)
        lines = out.splitlines()
        check(code == 0 and [l.split("\t")[:2] for l in lines] ==
              [["video", "NEWvideo001"], ["short", "NEWshort001"]],
              f"check: {out!r} {err!r}")
        notes = calls()
        check(notes == [
            ["--app-name=vts", "--", "Kurzgesagt \u2013 In a Nutshell",
             "A New Video &amp; &lt;b&gt;Bold&lt;/b&gt;"],
            ["--app-name=vts", "--",
             "Kurzgesagt \u2013 In a Nutshell (Short)", "A New Short"]],
            f"check: notifications {notes!r}")

        # nothing new the next time
        code, out, err = run(vts, "check", "--json", env=env)
        check(code == 0 and json.loads(out) == [] and calls() == [],
              f"check again: {out!r}")

        # no Shorts: their feed without them; notifications off
        run(vts, "fav", "set", "@kurzgesagt", "shorts=no", "notify=no",
            env=env)
        code, out, err = run(vts, "check", "-q", env=env)
        check(code == 0 and out == "" and calls() == [],
              f"check quiet: {out!r} {err!r}")

        # the feed can't be read: a failure, said
        csv = os.path.join(work, "subs.csv")
        with open(csv, "w") as f:
            f.write("Channel Id,Channel Url,Channel Title\n"
                    "UCXuqSBlHAE6Xw-yeJA0Tunw,http://x,Linus Tech Tips\n")
        run(vts, "fav", "import", csv, env=env)
        run(vts, "fav", "remove", "@kurzgesagt", env=env)
        code, out, err = run(vts, "check", env=env)
        check(code == 1 and "its feed can't be read" in err,
              f"check, no feed: {code} {err!r}")

        # the timer
        code, out, err = run(vts, "check", "--install", "15", env=env)
        units = os.path.join(work, "cconfig", "systemd", "user")
        with open(os.path.join(units, "vts-check.service")) as f:
            service = f.read()
        with open(os.path.join(units, "vts-check.timer")) as f:
            timer = f.read()
        check(code == 0 and f'ExecStart="{os.path.realpath(vts)}" check '
              "--quiet" in service and "OnUnitActiveSec=15min" in timer,
              f"install: {code} {err!r} {service!r}")
        check(calls() == [["--user", "daemon-reload"],
                          ["--user", "enable", "--now", "vts-check.timer"]],
              "install: systemctl")
        code, out, err = run(vts, "check", "--uninstall", env=env)
        check(code == 0 and not os.path.exists(
            os.path.join(units, "vts-check.timer")) and
            calls()[0] == ["--user", "disable", "--now", "vts-check.timer"],
            f"uninstall: {err!r}")
        code, out, err = run(vts, "check", "--install", "1", env=env)
        check(code == 2, "install: too often")
    finally:
        yt.stop()


def teach_pyte(pyte):
    """
    pyte doesn't know CSI S and T (scroll up, scroll down), which ncurses
    sends to move a part of the screen: without them its picture keeps
    lines that are gone from a real terminal
    """
    if "S" in pyte.Stream.csi:
        return

    def scroll(self, n=1, down=False):
        top, bottom = (self.margins.top, self.margins.bottom) \
            if self.margins else (0, self.lines - 1)
        x, y = self.cursor.x, self.cursor.y
        self.cursor.y = top if down else bottom
        for _ in range(max(1, n)):
            (self.reverse_index if down else self.index)()
        self.cursor.x, self.cursor.y = x, y

    pyte.Screen.scroll_up = lambda self, n=1, *a, **k: scroll(self, n)
    pyte.Screen.scroll_down = lambda self, n=1, *a, **k: scroll(self, n,
                                                                 True)
    pyte.Stream.csi = dict(pyte.Stream.csi, S="scroll_up", T="scroll_down")


class Tui:
    """vts on a pseudo terminal, its screen read with pyte"""

    def __init__(self, vts, args, env, cols=100, rows=30):
        import fcntl
        import struct
        import termios
        import pyte
        teach_pyte(pyte)
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(vts, [vts, *args], dict(env, TERM="xterm-256color"))
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))

    def pump(self, until=None, timeout=5.0):
        """read what vts draws, until @until is on the screen"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.stream.feed(os.read(self.fd, 65536))
                except OSError:
                    return False
            if until and until in self.text():
                return True
        return until is None

    def wait(self, cond, timeout=5.0):
        """read what vts draws until @cond() holds"""
        end = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > end:
                return False
            self.pump(None, 0.05)
        return True

    def keys(self, k, until=None, timeout=5.0):
        os.write(self.fd, k.encode())
        return self.pump(until, timeout)

    def text(self):
        return "\n".join(line.rstrip() for line in self.screen.display)

    def line(self, y):
        return self.screen.display[y].rstrip()

    def close(self):
        try:
            os.write(self.fd, b"1q")  # Home, where q quits
        except OSError:
            pass
        for _ in range(200):
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            time.sleep(0.05)
        os.kill(self.pid, 9)
        os.waitpid(self.pid, 0)
        return -9


def test_tui(vts, work):
    try:
        import pyte  # noqa: F401
    except ImportError:
        print("no pyte: the full screen app is not tested")
        return
    yt = FakeYouTube(work)
    fake = os.path.join(HERE, "fake_mpv.py")
    conf = os.path.join(work, "tui.conf")
    with open(conf, "w") as f:
        f.write(f"player = {fake}\ndownloader = {fake}\nthumbnails = no\n"
                "updates = no\n")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home5"), "LANG": "en_US.UTF-8",
           "XDG_DATA_HOME": os.path.join(work, "tdata"),
           "XDG_CACHE_HOME": os.path.join(work, "tcache"),
           "XDG_RUNTIME_DIR": os.path.join(work, "trun"),
           "VTS_BASE_URL": yt.url, "FAKE_PLAN": "[[30, 600]]",
           "FAKE_WAIT": "1", "FAKE_LOG": os.path.join(work, "tui.log")}
    logf = env["FAKE_LOG"]
    failf = os.path.join(work, "dl-fail")
    env["FAKE_DL_FAIL_FILE"] = failf
    t = Tui(vts, ["-c", conf], env)
    try:
        check(t.pump("No favourite channels yet."), f"home: {t.text()!r}")
        check(t.line(0).startswith(" vts  YouTube  Twitch  SOOP  \u203a "
                                   "Home"), f"home: top bar {t.line(0)!r}")

        # search, with suggestions while typing
        t.keys("/")
        check(t.keys("linus tor", "linus torvalds interview"),
              f"suggestions: {t.text()!r}")
        check(t.pump("[31mred") and "\x1b" not in t.text(),
              "suggestions: cleaned")
        t.keys("\x15linus torvalds\r",
               "Building the PERFECT Linux PC with Linus Torvalds")
        check(t.wait(lambda: t.line(1) == "\u258cBuilding the PERFECT "
                     "Linux PC with Linus Torvalds" and t.line(2).startswith(
                         "   53:44 \u00b7 Linus Tech Tips")),
              f"results: {t.line(1)!r} {t.line(2)!r}")

        # the video page, its comments, back
        check(t.keys("\r", "382K likes"), f"video: {t.text()!r}")
        check(t.pump("Up next") and "42 chapters (t)" in t.text(),
              "video: chapters and up next")
        check(t.keys("C", "22,478 Comments"), f"comments: {t.text()!r}")
        check(t.pump("pinned \u00b7 @LinusTechTips \u00b7 creator"),
              "comments: the pinned one")
        t.keys("\x7f", "382K likes")
        t.keys("\x7f", "Search: linus torvalds")

        # Watch later, then the library
        check(t.keys("w", "Added to Watch later"), "watch later")
        t.keys("3", "Library")
        check(t.pump("1 video"), f"library: {t.text()!r}")
        t.keys("\r")
        t.wait(lambda: "\u203a Watch later" in t.line(0))
        check(t.pump("Building the PERFECT Linux PC"), "watch later list")

        # play: mpv starts, the top bar follows it, x stops it
        check(t.keys("p", "\u25b6", 8), f"playing: {t.line(0)!r}")
        check(t.pump("0:30/10:00", 8), f"position: {t.line(0)!r}")
        check(t.keys("x", "Stopped"), f"stop: {t.text()!r}")
        check(t.pump("Building the PERFECT"), "stop: still listed")
        t.keys("3", "Library")
        t.keys("G\r")
        t.wait(lambda: "\u203a History" in t.line(0))
        check(t.pump("stopped at 0:30"), f"history: {t.text()!r}")

        # Q: play in a quality from a menu; V: go on in another one
        # (the menu starts on the quality of the settings: 1080p)
        t.keys("Q", "Play in which quality?")
        check(t.keys("jj\r", "Playing, 480p"), f"Q: {t.text()!r}")
        check(t.pump("0:30/10:00", 8), f"Q: {t.line(0)!r}")
        t.keys("V", "Go on in which quality?")
        check(t.keys("k\r", "Playing in 720p (HD)"), f"V: {t.text()!r}")
        check(t.pump("0:30/10:00", 8), f"V: {t.line(0)!r}")
        t.keys("x", "Stopped")
        with open(logf) as f:
            runs = [e["argv"] for e in map(json.loads, f) if "argv" in e]
        formats = [a for r in runs for a in r
                   if a.startswith("--ytdl-format=")]
        check(formats[-2:] == [
            "--ytdl-format=bestvideo[height<=?480]+bestaudio/"
            "best[height<=?480]/best",
            "--ytdl-format=bestvideo[height<=?720]+bestaudio/"
            "best[height<=?720]/best"], f"Q and V: {formats!r}")
        # V goes on from where it was
        check(any(a.startswith("--start=") for a in runs[-1]),
              f"V: from where it was {runs[-1]!r}")

        # d: the video in a quality, or the sound, followed in the top bar
        t.keys("d", "Sound only, MP3")
        # then in which sound quality (Medium)
        t.keys("G\r", "Sound quality?")
        check(t.keys("j\r", "Downloading (Sound only, FLAC, medium "
                     "quality)"), f"d: {t.text()!r}")
        check(t.wait(lambda: "\u2b07 50% 1.3 MB/s 0:02 left" in t.line(0)),
              f"d: progress {t.line(0)!r}")
        check(t.wait(lambda: "\u2b07 finishing" in t.line(0)),
              f"d: finishing {t.line(0)!r}")
        check(t.pump("\u2713 Downloaded: Building the PERFECT Linux PC"),
              f"d: done {t.text()!r}")
        check("\u2b07" not in t.line(0), f"d: gone {t.line(0)!r}")
        with open(logf) as f:
            runs = [e["argv"] for e in map(json.loads, f) if "argv" in e]
        check(runs[-1][:2] == ["--no-playlist", "-f"] and
              "--audio-format" in runs[-1] and "flac" in runs[-1] and
              "--newline" in runs[-1], f"d: the sound as FLAC {runs[-1]!r}")
        check(runs[-1][runs[-1].index("-S") + 1] == "abr~128" and
              runs[-1][runs[-1].index("--audio-quality") + 1] == "0",
              f"d: medium, FLAC at its best {runs[-1]!r}")
        t.keys("d", "Video, 720p (HD)")
        t.keys("\x1b", None)
        t.pump(None, 0.5)
        with open(logf) as f:
            check(sum("argv" in json.loads(l) for l in f) == len(runs),
                  "d: Esc downloads nothing")
        # quitting while a download goes on: asked first (No stays)
        t.keys("d", "Video, 720p (HD)")
        t.keys("\r", "Downloading")
        t.keys("1", timeout=0.2)
        t.wait(lambda: "\u203a Home" in t.line(0))
        t.keys("q", timeout=0.1)
        check(t.wait(lambda: "stop it and quit?" in t.text()) and
              "A download is not finished (" in t.text(),
              f"quit while downloading: {t.text()!r}")
        t.keys("\r")
        check(t.pump("\u2713 Downloaded:"), f"No: it goes on {t.text()!r}")
        t.keys("3", "Library")
        t.keys("G\r")
        t.wait(lambda: "\u203a History" in t.line(0))

        # a failure says why
        with open(failf, "w") as f:
            f.write("1")
        t.keys("d", "Video, 720p (HD)")
        check(t.keys("\r", "Download failed, [youtube] dQw4w9WgXcQ: Video "
                     "unavailable: Building the PERFECT"),
              f"d: failed, and why {t.text()!r}")
        os.unlink(failf)

        # u: the subtitles of what plays, from those the video has
        t.keys("p", "Playing")
        check(t.pump("0:30/10:00", 8), f"u: playing {t.line(0)!r}")
        check(t.keys("u", "German (Germany)"), f"u: menu {t.text()!r}")
        check(t.wait(lambda: "English (auto-generated)" in t.text()) and
              "x;rm" not in t.text() and "Live chat" not in t.text() and
              "\x1b" not in t.text(), f"u: the choices {t.text()!r}")
        t.keys("jj\r", "Subtitles: German (Germany)")
        check(t.pump("0:30/10:00", 8), f"u: goes on {t.line(0)!r}")
        t.keys("u", "No subtitles")
        check(t.keys("g\r", "Subtitles: No subtitles"), f"u off {t.text()!r}")
        t.pump("0:30/10:00", 8)
        t.keys("x", "Stopped")
        with open(logf) as f:
            runs = [e["argv"] for e in map(json.loads, f) if "argv" in e]
        plays = [r for r in runs if any(a.startswith("--input-ipc-server")
                                        for a in r)]
        check("--slang=de-DE" in plays[-2] and
              "--ytdl-raw-options-append=sub-langs=de-DE" in plays[-2] and
              any(a.startswith("--start=") for a in plays[-2]),
              f"u: mpv told {plays[-2]!r}")
        check("--sid=no" in plays[-1] and
              not any(a.startswith("--slang") for a in plays[-1]),
              f"u off: mpv told {plays[-1]!r}")

        # - removes from a list
        t.keys("3", "Library")
        t.keys("g\r")
        t.wait(lambda: "\u203a Watch later" in t.line(0))
        t.keys("-", "Nothing here.")
        t.keys("3", "Library")
        check(t.pump("0 videos"), f"removed: {t.text()!r}")

        # the search box: Esc, or Backspace with nothing typed, leaves it
        t.keys("/", "Esc or Backspace to cancel")
        t.keys("\x7f", timeout=0.1)
        check(t.wait(lambda: "Search:" not in t.text()),
              f"search box left: {t.text()!r}")
        t.keys("2", "No favourite channels yet")
        check("\u203a Favourites" in t.line(0), f"2: {t.line(0)!r}")
        t.keys("3", "Library")

        # help, then back to Home
        check(t.keys("?", "While playing"), f"help: {t.text()!r}")
        t.keys(" ", "Library")
        t.keys("1", "Home")
        code = t.close()
        check(code == 0, f"quit: {code} {t.text()!r}")
    finally:
        if t.pid:
            try:
                os.kill(t.pid, 9)
            except OSError:
                pass
        yt.stop()


def test_thumbnails(vts, work):
    if os.environ.get("VTS_TEST_THUMBNAILS", "1") != "1":
        print("built without thumbnails: not tested")
        return
    try:
        import pyte  # noqa: F401
    except ImportError:
        return
    yt = FakeYouTube(work)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home6"), "LANG": "en_US.UTF-8",
           "XDG_DATA_HOME": os.path.join(work, "tdata6"),
           "XDG_CACHE_HOME": os.path.join(work, "tcache6"),
           "XDG_RUNTIME_DIR": os.path.join(work, "trun6"),
           "VTS_BASE_URL": yt.url, "VTS_IMAGE_URL": yt.url,
           "COLORTERM": "truecolor"}
    blocks = set(chr(c) for c in range(0x2580, 0x25a0))

    def pane(t, top, bottom):
        return "".join(t.screen.display[y][90:] for y in range(top, bottom))

    t = Tui(vts, [], env, cols=130, rows=32)
    try:
        t.pump("No favourite channels yet.")
        t.keys("/")
        t.keys("linus torvalds\r", "Search: linus torvalds")
        # the preview: separator, picture, then the selected result
        check(t.pump("\u2502"), f"pane: {t.text()!r}")
        check(t.wait(lambda: blocks & set(pane(t, 2, 10))),
              f"picture: {pane(t, 1, 12)!r}")
        check("Building the PERFECT Linux PC" in pane(t, 10, 20),
              f"pane text: {pane(t, 1, 31)!r}")
        check(t.line(1).startswith("\u258cBuilding the PERFECT") and
              t.screen.display[1][89] == "\u2502", f"list: {t.line(1)!r}")
        # the next result: its own text in the pane
        t.keys("j")
        check(t.wait(lambda: "The Mind Behind Linux" in pane(t, 10, 20)),
              f"next: {pane(t, 1, 31)!r}")
        # the help hides the picture
        t.keys("?", "While playing")
        check(t.wait(lambda: not blocks & set(t.text())),
              f"help: {t.text()!r}")
        t.keys(" ", "Search: linus torvalds")
        check(t.wait(lambda: blocks & set(pane(t, 2, 10))),
              "picture back after the help")
        code = t.close()
        check(code == 0, f"quit: {code} {t.text()!r}")
    finally:
        try:
            os.kill(t.pid, 9)
        except OSError:
            pass
    # downloaded once, kept for next time
    cached = os.listdir(os.path.join(work, "tcache6", "vts", "thumbs"))
    check("mfv0V1SxbNA.jpg" in cached, f"cache: {cached}")

    # kitty: images drawn with its own protocol, deleted when they change
    conf = os.path.join(work, "kitty.conf")
    with open(conf, "w") as f:
        f.write("thumbnails = kitty\n")
    t = Tui(vts, ["-c", conf], env, cols=130, rows=32)
    raw = bytearray()
    try:
        feed = t.stream.feed

        def keep(data):
            raw.extend(data)
            feed(data)
        t.stream.feed = keep
        t.pump("No favourite channels yet.")
        t.keys("/")
        t.keys("linus torvalds\r", "Search: linus torvalds")
        end = time.monotonic() + 5
        while time.monotonic() < end and b"\x1b_G" not in raw:
            t.pump(None, 0.1)
        check(b"\x1b_Ga=T" in raw or b"\x1b_G" in raw, "kitty image")
        n = raw.count(b"\x1b_Ga=d")
        t.keys("j")
        t.pump(None, 1)
        check(raw.count(b"\x1b_Ga=d") > n, "kitty: the old image deleted")
        code = t.close()
        check(code == 0, f"kitty: quit: {code} {t.text()!r}")
    finally:
        try:
            os.kill(t.pid, 9)
        except OSError:
            pass
        yt.stop()


def test_update(vts, work):
    yt = FakeYouTube(work)
    fake = os.path.join(HERE, "fake_mpv.py")
    bindir = os.path.join(work, "bin7")
    os.makedirs(bindir)
    for name in ("mpv", "yt-dlp", "notify-send", "ffmpeg"):
        os.symlink(fake, os.path.join(bindir, name))
    logf = os.path.join(work, "update.log")
    cache = os.path.join(work, "ucache")
    env = {"PATH": bindir + ":" + os.environ.get("PATH", "/usr/bin:/bin"),
           "HOME": os.path.join(work, "home7"), "LANG": "en_US.UTF-8",
           "XDG_CONFIG_HOME": os.path.join(work, "uconfig"),
           "XDG_DATA_HOME": os.path.join(work, "udata"),
           "XDG_CACHE_HOME": cache,
           "XDG_RUNTIME_DIR": os.path.join(work, "urun"),
           "VTS_BASE_URL": yt.url, "VTS_IMAGE_URL": yt.url,
           "FAKE_LOG": logf, "TERM": "xterm-256color"}
    with open(os.path.join(HERE, "..", "vts", "youtube.json")) as f:
        builtin = json.load(f)
    rev = builtin["revision"]
    kept = os.path.join(cache, "vts", "rules.json")

    def publish(doc):
        body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
        req = urllib.request.Request(yt.url + "/rules.json", data=body,
                                     method="POST")
        urllib.request.urlopen(req, timeout=10).read()

    def asked():
        return int(yt.get("/rules-asked"))

    def updates():
        """the yt-dlp --update runs logged so far"""
        if not os.path.exists(logf):
            return 0
        with open(logf) as f:
            return sum(json.loads(l)["argv"] == ["--update"] for l in f)

    try:
        # nothing published
        code, out, err = run(vts, "update", "--rules", env=env)
        check(code == 1 and "nothing there (HTTP 404)" in err,
              f"update, none: {code} {out!r} {err!r}")
        check(not os.path.exists(kept), "update, none: nothing kept")

        # newer rules: taken, kept, used
        newer = dict(builtin, revision=rev + 1)
        publish(newer)
        code, out, err = run(vts, "update", "--rules", env=env)
        # (and Twitch's and SOOP's descriptions: those of this vts)
        sites_same = "twitch.json: up to date\nsoop.json: up to date\n"
        check(code == 0 and out == f"rules: revision {rev + 1} "
              f"(was {rev})\n" + sites_same,
              f"update: {code} {out!r} {err!r}")
        check(os.path.exists(kept), "update: kept")
        code, out, err = run(vts, "search", "linus torvalds", env=env)
        check(code == 0 and "Building the PERFECT" in out,
              f"update: search with them {err!r}")
        code, out, err = run(vts, "update", "--rules", env=env)
        check(code == 0 and out == f"rules: revision {rev + 1}, "
              "up to date\n" + sites_same, f"update again: {out!r} {err!r}")

        # older, broken or not JSON: refused, the kept ones stay
        with open(kept, "rb") as f:
            good = f.read()
        bad = dict(builtin, revision=rev + 5)
        del bad["watch"]
        for doc, what in ((dict(builtin, revision=rev - 1), "up to date"),
                          (bad, "(not used)"),
                          (b"{not json", "(not used)"),
                          (dict(bad, version=99), "(not used)")):
            publish(doc)
            code, out, err = run(vts, "update", "--rules", env=env)
            check(what in out + err, f"update {what}: {out!r} {err!r}")
            with open(kept, "rb") as f:
                check(f.read() == good, f"update {what}: kept unchanged")

        # a kept file damaged afterwards: the built-in rules, said once
        with open(kept, "w") as f:
            f.write("{")
        code, out, err = run(vts, "search", "linus torvalds", env=env)
        check(code == 0 and "built-in rules are used" in err and
              "Building the PERFECT" in out, f"damaged: {err!r}")
        os.unlink(kept)

        # yt-dlp updates itself when it's this user's own
        code, out, err = run(vts, "update", "--yt-dlp", env=env)
        check(code == 0 and "Updated yt-dlp" in out and updates() == 1,
              f"update yt-dlp: {code} {out!r} {err!r}")
        if os.geteuid() != 0:
            # the system's: left to its packages
            sysdir = os.path.join(work, "sysbin")
            os.makedirs(sysdir)
            os.symlink(fake, os.path.join(sysdir, "yt-dlp"))
            os.chmod(sysdir, 0o555)
            e = dict(env, PATH=sysdir + ":" + env["PATH"])
            code, out, err = run(vts, "update", "--yt-dlp", env=e)
            check(code == 0 and "belongs to the system" in out and
                  updates() == 1, f"system yt-dlp: {out!r} {err!r}")
            os.chmod(sysdir, 0o755)
        code, out, err = run(vts, "--offline", "update", env=env)
        check(code == 1 and "--offline" in err, f"offline update: {err!r}")

        # vts check does it on its own: once a day, once a week
        for name in ("rules", "yt-dlp"):
            os.unlink(os.path.join(cache, "vts", "checked-" + name))
        publish(newer)
        n = asked()
        code, out, err = run(vts, "check", "-q", env=env)
        check(code == 0 and asked() == n + 1 and updates() == 2 and
              os.path.exists(kept), f"check: {code} {err!r}")
        code, out, err = run(vts, "check", "-q", env=env)
        check(asked() == n + 1 and updates() == 2, "check: not again")
        stamp = os.path.join(cache, "vts", "checked-rules")
        with open(stamp, "w") as f:
            f.write(str(int(time.time()) - 25 * 3600) + "\n")
        run(vts, "--offline", "check", "-q", env=env)
        conf = os.path.join(work, "noupdates.conf")
        with open(conf, "w") as f:
            f.write("updates = no\n")
        run(vts, "-c", conf, "check", "-q", env=env)
        check(asked() == n + 1, "check: not with --offline or updates = no")
        run(vts, "check", "-q", env=env)
        check(asked() == n + 2 and updates() == 2, "check: the next day")

        # the doctor
        code, out, err = run(vts, "doctor", env=env)
        lines = out.splitlines()
        check(code == 0 and lines[0].startswith("vts ") and
              lines[-1] in ("Everything works.", "Works, 1 warning."),
              f"doctor: {code} {out!r} {err!r}")
        today = time.strftime("%Y.%m.%d", time.gmtime())
        for want in ("ok  player       mpv " + today,
                     "ok  yt-dlp       " + today,
                     "ok  notifier     " + os.path.join(bindir, "notify-send"),
                     "ok  ffmpeg       " + os.path.join(bindir, "ffmpeg"),
                     f"ok  rules        revision {rev + 1}, updated",
                     "ok  search       40 results, web client",
                     "ok  video page   ",
                     "ok  feeds        15 videos from Kurzgesagt",
                     "ok  suggestions  4"):
            check(any(want in l for l in lines), f"doctor: {want!r} in {out}")
        old = time.strftime("%Y.%m.%d", time.gmtime(time.time() - 90 * 86400))
        e = dict(env, FAKE_VERSION=old)
        code, out, err = run(vts, "doctor", env=e)
        check(f"!!  yt-dlp       {old} (" in out and "is 90 days old" in out
              and "vts update --yt-dlp" in out, f"doctor, old: {out!r}")
        e = dict(env, FAKE_VERSION="2024.04.09")
        code, out, err = run(vts, "doctor", env=e)
        check(code == 1 and "XX  yt-dlp       2024.04.09 (" in out,
              f"doctor, far too old: {out!r}")
        conf = os.path.join(work, "nompv7.conf")
        with open(conf, "w") as f:
            f.write("player = no-such-player\n")
        code, out, err = run(vts, "-c", conf, "doctor", env=env)
        check(code == 1 and "XX  player       no-such-player is not "
              "installed" in out and "1 problem" in out,
              f"doctor, no player: {code} {out!r}")
        code, out, err = run(vts, "doctor", "extra", env=env)
        check(code == 2, f"doctor extra: {code}")

        # the app says at once when yt-dlp is too old to play anything
        conf = os.path.join(work, "app7.conf")
        with open(conf, "w") as f:
            f.write(f"player = {fake}\nthumbnails = no\nupdates = no\n")
        e = dict(env, FAKE_VERSION="2024.04.09")
        t = Tui(vts, ["-c", conf], e)
        try:
            check(t.pump("too old to play YouTube"),
                  f"app: old yt-dlp {t.text()!r}")
            code = t.close()
            check(code == 0, f"app: old yt-dlp, quit: {code}")
        finally:
            try:
                os.kill(t.pid, 9)
            except OSError:
                pass
        # (its version is kept until the program changes: forget it)
        os.unlink(os.path.join(cache, "vts", "yt-dlp-version"))
        code, out, err = run(vts, "play", "dQw4w9WgXcQ", env=e)
        check("yt-dlp 2024.04.09 is " in err and "too old" in err,
              f"play, old yt-dlp: {err!r}")
        os.unlink(os.path.join(cache, "vts", "yt-dlp-version"))
        t = Tui(vts, ["-c", conf], env)
        try:
            t.pump("No favourite channels yet.")
            t.pump(None, 0.5)
            check("too old" not in t.text() and
                  "not installed" not in t.text(),
                  f"app: recent yt-dlp {t.text()!r}")
            t.close()
        finally:
            try:
                os.kill(t.pid, 9)
            except OSError:
                pass

        # the app does it in the background
        os.unlink(kept)
        os.unlink(stamp)
        conf = os.path.join(work, "app7.conf")
        with open(conf, "w") as f:
            f.write(f"player = {fake}\nthumbnails = no\n")
        t = Tui(vts, ["-c", conf], env)
        try:
            t.pump("No favourite channels yet.")
            check(t.wait(lambda: os.path.exists(kept)), "app: rules updated")
            code = t.close()
            check(code == 0, f"app: quit: {code} {t.text()!r}")
        finally:
            try:
                os.kill(t.pid, 9)
            except OSError:
                pass
    finally:
        yt.stop()


def main():
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    vts, version = sys.argv[1:]
    for t in (test_version, test_help, test_bad_usage):
        before = failures
        t(vts, version)
        print(f"{t.__name__:52} {'ok' if failures == before else 'FAIL'}")
    work = tempfile.mkdtemp(prefix="vts-cli-")
    for t in (test_config, test_search, test_play, test_check, test_tui,
              test_thumbnails, test_update):
        before = failures
        t(vts, work)
        print(f"{t.__name__:52} {'ok' if failures == before else 'FAIL'}")
    if failures:
        print(f"files kept in {work}")
    else:
        shutil.rmtree(work)
    print(f"{checks} checks, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

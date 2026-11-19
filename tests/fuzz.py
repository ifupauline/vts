#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Feed vts broken and hostile versions of YouTube's answers, feeds and
imported files, and watch it fail cleanly: an error message and exit
status 1 are fine, a Python traceback (an exception nothing caught) or a
hang are not.

usage: fuzz.py VTS [ROUNDS] [SEED]

VTS is the program to run (make fuzz makes one that runs this folder's
vts). Each round takes one recorded answer (tests/fixtures/youtube),
damages it (a JSON value of the wrong type, a key removed, deep nesting, a huge or
hostile string, cut short, bytes flipped...), serves it from the test
server and runs the command that reads it. What made vts fail is kept in
fuzz-crash-N/ in the current folder, with the command line.
"""
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "youtube")
KURZ = "UCsXVk37bltHxD1rDPwtNM8Q"

# fixture: the command that reads it
TARGETS = {
    "search-linus.json": ["search", "--json", "linus torvalds"],
    "search-channels.json": ["search", "-t", "channel", "kurzgesagt"],
    "search-playlists.json": ["search", "-t", "playlist", "kurzgesagt"],
    "next-mfv0V1SxbNA.json": ["info", "--json", "mfv0V1SxbNA"],
    "comments-mfv0V1SxbNA.json": ["comments", "--json", "--replies",
                                  "mfv0V1SxbNA"],
    "replies-mfv0V1SxbNA.json": ["comments", "--replies", "mfv0V1SxbNA"],
    "channel-videos.json": ["channel", "--json", KURZ],
    "channel-shorts.json": ["channel", "-t", "shorts", KURZ],
    "channel-playlists.json": ["channel", "-t", "playlists", KURZ],
    "resolve-kurzgesagt.json": ["channel", "@kurzgesagt"],
    "playlist-existential.json": ["playlist", "--json",
                                  "PLFs4vir_WsTxontcYm5ctqp89cNBJKNrs"],
    "feed-%s.xml" % KURZ: ["feed", "--json", KURZ],
    "suggest.json": ["suggest", "linus tor"],
    "home.html": ["search", "linus torvalds"],
}
HOSTILE = ["\x1b]52;c;aGk=\x07", "\x1b[2J", "\u202e", "\x00", "\ud800",
           "%s%n%x", "-o", "--exec=sh", "../../etc/passwd", "A" * 70000,
           "\U0001F600" * 500, "javascript:alert(1)", "https://evil.example/",
           "<b>&amp;</b>", "\r\n"]


def mutate_json(doc, rnd):
    """damage one random place of @doc"""
    nodes = []

    def walk(o, depth):
        if isinstance(o, dict):
            for k in list(o):
                nodes.append((o, k))
                walk(o[k], depth + 1)
        elif isinstance(o, list):
            for i in range(len(o)):
                nodes.append((o, i))
                walk(o[i], depth + 1)
    walk(doc, 0)
    for _ in range(rnd.randint(1, 8)):
        if not nodes:
            break
        parent, key = rnd.choice(nodes)
        try:
            value = parent[key]
        except (KeyError, IndexError):
            continue
        choice = rnd.randrange(9)
        if choice == 0 and isinstance(parent, dict):
            del parent[key]
        elif choice == 1:
            parent[key] = rnd.choice([None, True, 0, -1, 1e308, "", [], {}])
        elif choice == 2:
            parent[key] = rnd.choice(HOSTILE)
        elif choice == 3:
            deep = value
            for _ in range(rnd.choice([50, 200, 2000])):
                deep = [deep] if rnd.random() < 0.5 else {"x": deep}
            parent[key] = deep
        elif choice == 4 and isinstance(value, str):
            parent[key] = value + rnd.choice(HOSTILE)
        elif choice == 5 and isinstance(value, list) and value:
            parent[key] = value * rnd.choice([2, 50])
        elif choice == 6:
            parent[key] = {"runs": [{"text": rnd.choice(HOSTILE)}]}
        elif choice == 7 and isinstance(value, (int, float)):
            parent[key] = rnd.choice([-2**63, 2**64, 1e-300])
        else:
            parent[key] = rnd.choice([value, [value], {"a": value}])
    return doc


def mutate_bytes(data, rnd):
    data = bytearray(data)
    for _ in range(rnd.randint(1, 20)):
        if not data:
            break
        i = rnd.randrange(len(data))
        op = rnd.randrange(4)
        if op == 0:
            data[i] = rnd.randrange(256)
        elif op == 1:
            del data[i:i + rnd.randint(1, 64)]
        elif op == 2:
            data[i:i] = rnd.choice(HOSTILE).encode("utf-8", "surrogatepass")
        else:
            data = data[:i]
    return bytes(data)


def mutate(name, data, rnd):
    if name.endswith(".json") and rnd.random() < 0.8:
        try:
            doc = json.loads(data)
        except ValueError:
            return mutate_bytes(data, rnd)
        text = json.dumps(mutate_json(doc, rnd), ensure_ascii=rnd.random()
                          < 0.5)
        return text.encode("utf-8", "surrogatepass")
    if name.endswith(".xml") and rnd.random() < 0.5:
        text = data.decode()
        pick = rnd.choice([
            lambda t: t.replace("<title>", "<title>" + rnd.choice(
                ["&amp;&lt;", "\x1b[31m", "<![CDATA[\x07]]>", "&#27;"]), 1),
            lambda t: t.replace("<entry>", "<entry>" * 300, 1),
            lambda t: '<!DOCTYPE x [<!ENTITY a "aaaa">]>' + t,
            lambda t: t.replace("yt:videoId>", "yt:videoId>../", 1),
            lambda t: t[:len(t) // 2],
        ])
        return pick(text).encode()
    return mutate_bytes(data, rnd)


def main():
    if len(sys.argv) not in (2, 3, 4):
        sys.exit(__doc__.strip())
    vts = os.path.abspath(sys.argv[1])
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else int(time.time())
    rnd = random.Random(seed)
    print(f"seed {seed}, {rounds} rounds")
    work = tempfile.mkdtemp(prefix="vts-fuzz-")
    fixtures = os.path.join(work, "fixtures")
    shutil.copytree(FIXTURES, fixtures)
    port = os.path.join(work, "port")
    server = subprocess.Popen([sys.executable,
                               os.path.join(HERE, "fake_server.py"), port,
                               fixtures])
    while not os.path.exists(port):
        time.sleep(0.05)
    url = "http://127.0.0.1:" + open(port).read().strip()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": work,
           "LANG": "en_US.UTF-8", "VTS_BASE_URL": url,
           "XDG_CACHE_HOME": os.path.join(work, "cache"),
           "XDG_DATA_HOME": os.path.join(work, "data"),
           "XDG_CONFIG_HOME": os.path.join(work, "config")}
    crashes = 0
    try:
        for n in range(rounds):
            name = rnd.choice(sorted(TARGETS))
            path = os.path.join(fixtures, name)
            with open(os.path.join(FIXTURES, name), "rb") as f:
                good = f.read()
            bad = mutate(name, good, rnd)
            with open(path, "wb") as f:
                f.write(bad)
            # the web client version is cached: read the home page again
            shutil.rmtree(env["XDG_CACHE_HOME"], ignore_errors=True)
            args = [vts, "--offline"] + TARGETS[name]
            try:
                p = subprocess.run(args, capture_output=True, timeout=30,
                                   env=env)
                # an error said is fine; a Python traceback is a bug
                failed = p.returncode not in (0, 1) or \
                    b"Traceback (most recent call last)" in p.stderr
                why = p.stderr.decode(errors="replace")[-3000:]
            except subprocess.TimeoutExpired:
                failed, why = True, "timed out"
            with open(path, "wb") as f:
                f.write(good)
            if failed:
                crashes += 1
                keep = f"fuzz-crash-{crashes}"
                os.makedirs(keep, exist_ok=True)
                with open(os.path.join(keep, name), "wb") as f:
                    f.write(bad)
                with open(os.path.join(keep, "command"), "w") as f:
                    f.write(" ".join(args[1:]) + "\n" + why)
                print(f"round {n}: {name}: {why.splitlines()[-1:]}",
                      flush=True)
            elif n % 100 == 99:
                print(f"round {n + 1}: fine so far", flush=True)
    finally:
        server.terminate()
        shutil.rmtree(work, ignore_errors=True)
    print(f"{crashes} failure(s) in {rounds} rounds (seed {seed})")
    return 1 if crashes else 0


if __name__ == "__main__":
    sys.exit(main())

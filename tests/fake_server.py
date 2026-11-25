#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
A local HTTP server for the tests, so they never depend on the internet.
It listens on 127.0.0.1, on a free port written to PORTFILE once ready.

usage: fake_server.py PORTFILE [FIXTURES]

  /echo             the request as JSON: method, path, headers, body
  /status/N         an answer with status N
  /big/N            N bytes
  /gzip             a gzip-compressed answer, when the client accepts it
  /redirect?to=URL  a redirection to URL
  /loop             a redirection to itself
  /slow/MS          an answer after MS milliseconds
  /flaky/KEY/N      503 for the first N requests with this KEY, then 200
  /hangup           closes the connection without answering
  /connections      how many connections were opened so far

With FIXTURES (tests/fixtures/youtube), it also plays YouTube:

  GET /                       FIXTURES/home.html (the web client's version)
  POST /youtubei/v1/ENDPOINT  the recorded answer that index.json gives
                              for this request, or a 404 error
  GET /complete/search        FIXTURES/suggest.json
  GET /feeds/videos.xml?channel_id=ID (or playlist_id=ID)
                              FIXTURES/feed-ID.xml, or a 404
  GET /set-feed?id=ID&file=F  serve FIXTURES/F as the feed of ID from now on
  GET /vi/ID/mqdefault.jpg    FIXTURES/thumbnail.jpg (an HTML page for
                              notajpeg123, a 404 for notfound123)
  GET /thumbnails             the IDs of the thumbnails asked for so far
  GET /rules.json             the rules last POSTed there, or a 404
  GET /rules-asked            how many times they were asked for
  GET /last-request           the body of the last POST, as received
  GET /requests               how many POSTs were received so far
"""
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
flaky_counts = {}
connections = set()
fixtures = None
feed_files = {}
last_request = b"{}"
posts = 0
thumbs = []
rules = [None]
rules_asked = [0]


def fixture_for(endpoint, body):
    """the index entry whose match fits the request, or None"""
    try:
        req = json.loads(body)
    except ValueError:
        return None
    with open(os.path.join(fixtures, "index.json")) as f:
        index = json.load(f)
    hl = req.get("context", {}).get("client", {}).get("hl")
    if endpoint == "twitch":
        # Twitch's GraphQL: matched on the query's variables
        req = req.get("variables") or {}
    for e in index["requests"]:
        if e["endpoint"] != endpoint:
            continue
        if all((hl if k == "hl" else req.get(k)) == v
               for k, v in e["match"].items()):
            return e
    return None


def read_fixture(name):
    with open(os.path.join(fixtures, name), "rb") as f:
        return f.read()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive

    def log_message(self, fmt, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send(self, status, body=b"", headers=()):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_any(self):
        with lock:
            connections.add(self.client_address)
        url = urllib.parse.urlsplit(self.path)
        parts = url.path.strip("/").split("/")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        global last_request, posts
        if fixtures and self.command == "POST" and \
                url.path.startswith("/youtubei/v1/"):
            with lock:
                last_request = body
                posts += 1
            e = fixture_for(url.path[len("/youtubei/v1/"):], body)
            if e:
                ctype = "text/html" if e["file"].endswith(".html") \
                    else "application/json"
                self.send(e.get("status", 200), read_fixture(e["file"]),
                          [("Content-Type", ctype)])
            else:
                self.send(404, json.dumps({"error": {
                    "code": 404, "message": "no fixture for this request"}}))
        elif fixtures and self.command == "POST" and url.path == "/gql":
            with lock:
                last_request = body
                posts += 1
            e = fixture_for("twitch", body)
            if e:
                self.send(e.get("status", 200), read_fixture(e["file"]),
                          [("Content-Type", "application/json")])
            else:
                self.send(200, json.dumps({"data": {"users": []}}))
        elif fixtures and url.path == "/" and self.command == "GET":
            self.send(200, read_fixture("home.html"),
                      [("Content-Type", "text/html")])
        elif fixtures and url.path == "/feeds/videos.xml":
            q = urllib.parse.parse_qs(url.query)
            fid = (q.get("channel_id") or q.get("playlist_id") or [""])[0]
            with lock:
                name = feed_files.get(fid, f"feed-{fid}.xml")
            if re.fullmatch(r"[A-Za-z0-9_-]+", fid) and \
                    os.path.exists(os.path.join(fixtures, name)):
                self.send(200, read_fixture(name),
                          [("Content-Type", "text/xml; charset=UTF-8")])
            else:
                self.send(404, "<html>not found</html>")
        elif fixtures and len(parts) == 3 and parts[0] == "vi" and \
                parts[2] == "mqdefault.jpg":
            with lock:
                thumbs.append(parts[1])
            if parts[1] == "notajpeg123":
                self.send(200, "<html>a page</html>",
                          [("Content-Type", "text/html")])
            elif parts[1] == "notfound123":
                self.send(404, read_fixture("thumbnail.jpg"),
                          [("Content-Type", "image/jpeg")])
            else:
                self.send(200, read_fixture("thumbnail.jpg"),
                          [("Content-Type", "image/jpeg")])
        elif url.path == "/rules.json" and self.command == "GET":
            with lock:
                rules_asked[0] += 1
                text = rules[0]
            if text is None:
                self.send(404, "not found")
            else:
                self.send(200, text, [("Content-Type", "application/json")])
        elif url.path == "/rules.json":
            with lock:
                rules[0] = body if body else None
            self.send(200, "ok")
        elif url.path == "/rules-asked":
            with lock:
                self.send(200, str(rules_asked[0]))
        elif url.path == "/thumbnails":
            with lock:
                self.send(200, " ".join(thumbs))
        elif fixtures and url.path == "/complete/search":
            self.send(200, read_fixture("suggest.json"),
                      [("Content-Type", "text/javascript")])
        elif url.path == "/set-feed":
            q = urllib.parse.parse_qs(url.query)
            with lock:
                feed_files[q["id"][0]] = os.path.basename(q["file"][0])
            self.send(200, "ok")
        elif url.path == "/last-request":
            with lock:
                self.send(200, last_request)
        elif url.path == "/requests":
            with lock:
                self.send(200, str(posts))
        elif parts[0] == "echo":
            self.send(200, json.dumps({
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body.decode(errors="replace"),
            }), [("Content-Type", "application/json")])
        elif parts[0] == "status":
            code = int(parts[1])
            # these never have a body (RFC 9110)
            body = "" if code in (204, 304) else f"status {code}"
            self.send(code, body)
        elif parts[0] == "big":
            self.send(200, b"x" * int(parts[1]))
        elif parts[0] == "gzip":
            text = b"compressed hello\n" * 1000
            if "gzip" in (self.headers.get("Accept-Encoding") or ""):
                self.send(200, gzip.compress(text),
                          [("Content-Encoding", "gzip")])
            else:
                self.send(200, text)
        elif parts[0] == "redirect":
            to = urllib.parse.parse_qs(url.query)["to"][0]
            self.send(302, "", [("Location", to)])
        elif parts[0] == "loop":
            self.send(302, "", [("Location", "/loop")])
        elif parts[0] == "slow":
            time.sleep(int(parts[1]) / 1000)
            self.send(200, "slow")
        elif parts[0] == "flaky":
            key, fails = parts[1], int(parts[2])
            with lock:
                flaky_counts[key] = flaky_counts.get(key, 0) + 1
                n = flaky_counts[key]
            if n <= fails:
                self.send(503, f"attempt {n}")
            else:
                self.send(200, f"attempt {n}")
        elif parts[0] == "hangup":
            self.close_connection = True
        elif parts[0] == "connections":
            with lock:
                self.send(200, str(len(connections)))
        else:
            self.send(404, "not found")

    do_GET = handle_any
    do_POST = handle_any


def main():
    global fixtures
    if len(sys.argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if len(sys.argv) == 3:
        fixtures = sys.argv[2]
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    tmp = sys.argv[1] + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(server.server_address[1]))
    os.rename(tmp, sys.argv[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

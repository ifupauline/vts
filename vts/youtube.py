# SPDX-License-Identifier: Apache-2.0
"""
YouTube's web client, without an account: InnerTube calls as the website
makes them, its pages and feeds.
"""

import json
import os
import re
import time

from vts import http, log, platform, rules, text

DEFAULT_BASE = "https://www.youtube.com"
DEFAULT_SUGGEST_BASE = "https://suggestqueries.google.com"
CLIENT_VERSION = "2.20261111.01.00"
CLIENT_MAX_AGE = 24 * 3600
# "I made my choice" on the European consent page, nothing more
CONSENT_COOKIE = "SOCS=CAI"
HOME_MAX = 8 * 1024 * 1024
SUGGEST_MAX = 64 * 1024
BOT_CHECK_WAIT = 1.5
BOT_CHECK_MESSAGE = ("YouTube is refusing requests from this network for a "
                     "while (automated traffic check), try again later")

# kinds of failure
AGAIN = "again"  # the bot check: later
INVALID = "invalid"  # refused (4xx)
IO = "io"
NOT_FOUND = "not-found"
BAD_ANSWER = "bad-answer"
UNREACHABLE = "unreachable"


class YouTubeError(Exception):
    def __init__(self, message, kind=IO):
        super().__init__(message)
        self.kind = kind


def _version_ok(s):
    return 3 <= len(s) < 32 and all(c in "0123456789." for c in s)


def _key_ok(s):
    return 10 <= len(s) < 64 and rules.id_chars(s)


def _cache_path():
    d = platform.cache_dir()
    return os.path.join(d, "client") if d else None


def _is_bot_check(r):
    b = r.body[:200000]
    return r.status in (403, 429) and (b"<title>Sorry" in b or
                                       b"unusual traffic" in b or
                                       b"/sorry/" in b)


def base_url():
    base = os.environ.get("VTS_BASE_URL", "")
    if base.startswith(("http://", "https://")):
        return base
    return None


class Client:
    def __init__(self, cfg):
        base = base_url()
        self.base = base or DEFAULT_BASE
        self.suggest_base = base or DEFAULT_SUGGEST_BASE
        self.hl = cfg.language
        self.gl = cfg.region
        self.client_version = ""
        self.api_key = ""
        self._ready = False
        # the built-in rules, or newer ones from an update
        from vts import update
        self.rules, self.rules_updated = update.best_rules()

    def set_rules(self, r):
        self.rules = r
        self.rules_updated = True

    # --- the client version, from the home page or the cache -------------

    def _load_client(self):
        p = _cache_path()
        if not p:
            return False
        try:
            parts = platform.read_file(p, 256).split()
        except (OSError, ValueError):
            return False
        if len(parts) != 3 or not _version_ok(parts[0]) or (
                parts[1] != "-" and not _key_ok(parts[1])):
            return False
        try:
            age = int(time.time()) - int(parts[2])
        except ValueError:
            return False
        if age < 0 or age > CLIENT_MAX_AGE:
            return False
        self.client_version = parts[0]
        self.api_key = parts[1] if parts[1] != "-" else ""
        return True

    def _save_client(self):
        p = _cache_path()
        if not p:
            return
        try:
            platform.mkdirs(os.path.dirname(p))
            platform.write_atomic(p, f"{self.client_version} "
                                     f"{self.api_key or '-'} "
                                     f"{int(time.time())}\n")
        except OSError:
            pass

    def bootstrap(self):
        if self._ready:
            return
        self._ready = True
        if self._load_client():
            return
        self.client_version = CLIENT_VERSION
        self.api_key = ""
        r = http.fetch(http.Request(f"{self.base}/?hl={self.hl}",
                                    cookie=CONSENT_COOKIE, max_size=HOME_MAX,
                                    retries=1))
        if r.error or r.status != 200:
            log.dbg(f"youtube: home page unavailable ({r.message}), using "
                    f"{self.client_version}")
            return
        page = r.text()
        m = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^"]*)"', page)
        if m and _version_ok(m.group(1)):
            self.client_version = m.group(1)
            k = re.search(r'"INNERTUBE_API_KEY":"([^"]*)"', page)
            if k and _key_ok(k.group(1)):
                self.api_key = k.group(1)
            self._save_client()
        else:
            log.dbg("youtube: no client version on the home page")
        log.dbg(f"youtube: web client {self.client_version}")

    # --- calls -------------------------------------------------------------

    def call(self, endpoint, fields):
        """the JSON answer of an InnerTube call; YouTubeError"""
        self.bootstrap()
        client = {"clientName": "WEB", "clientVersion": self.client_version,
                  "hl": self.hl}
        if self.gl:
            client["gl"] = self.gl
        body = {"context": {"client": client}}
        body.update(fields)
        url = f"{self.base}/youtubei/v1/{endpoint}?prettyPrint=false"
        if self.api_key:
            url += f"&key={self.api_key}"
        req = http.Request(
            url, body=json.dumps(body, ensure_ascii=False).encode(),
            content_type="application/json", cookie=CONSENT_COOKIE,
            headers={"X-YouTube-Client-Name": "1",
                     "X-YouTube-Client-Version": self.client_version,
                     "Origin": self.base},
            retries=2)
        r = http.fetch(req)
        if not r.error and _is_bot_check(r):
            # often only one server of many: once more, a moment later
            log.dbg("youtube: automated traffic check, trying again")
            time.sleep(BOT_CHECK_WAIT)
            r = http.fetch(req)
        if r.error:
            raise YouTubeError(f"can't reach YouTube: {r.message}",
                               UNREACHABLE)
        if _is_bot_check(r):
            raise YouTubeError(BOT_CHECK_MESSAGE, AGAIN)
        try:
            doc = json.loads(r.body.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            doc = None
        if r.status != 200:
            why = None
            if isinstance(doc, dict) and isinstance(doc.get("error"), dict):
                why = doc["error"].get("message")
                why = text.clean(why) if isinstance(why, str) else None
            raise YouTubeError(
                f"YouTube refused the request (HTTP {r.status}"
                + (f": {why}" if why else "") + ")",
                INVALID if 400 <= r.status < 500 else IO)
        if not isinstance(doc, (dict, list)):
            raise YouTubeError("unexpected answer from YouTube (not JSON)",
                               BAD_ANSWER)
        return doc

    def get(self, path, max_size):
        """the page @path of YouTube (bytes); YouTubeError"""
        r = http.fetch(http.Request(self.base + path, cookie=CONSENT_COOKIE,
                                    max_size=max_size, retries=2))
        return self._page_of(r)

    def _page_of(self, r):
        if r.error:
            raise YouTubeError(f"can't reach YouTube: {r.message}",
                               UNREACHABLE)
        if _is_bot_check(r):
            raise YouTubeError(BOT_CHECK_MESSAGE, AGAIN)
        if r.status != 200:
            raise YouTubeError(f"YouTube answered HTTP {r.status}",
                               NOT_FOUND if r.status == 404 else IO)
        return r.body

    def get_all(self, paths, max_size, parallel):
        """the pages @paths, each bytes or a YouTubeError"""
        reqs = [http.Request(self.base + p, cookie=CONSENT_COOKIE,
                             max_size=max_size, retries=2) for p in paths]
        out = []
        for r in http.fetch_all(reqs, parallel):
            try:
                out.append(self._page_of(r))
            except YouTubeError as e:
                out.append(e)
        return out

    def suggest(self, words):
        """what the search box suggests for @words, ten at most"""
        url = (f"{self.suggest_base}/complete/search?client=firefox&ds=yt"
               f"&hl={http.escape(self.hl)}&q={http.escape(words)}")
        r = http.fetch(http.Request(url, max_size=SUGGEST_MAX, timeout=5.0))
        if r.error:
            raise YouTubeError(f"can't reach YouTube: {r.message}",
                               UNREACHABLE)
        try:
            doc = json.loads(r.body.decode("utf-8", "replace"))
        except (ValueError, RecursionError):
            doc = None
        # ["what was typed", ["suggestion", ...], ...]
        lst = doc[1] if isinstance(doc, list) and len(doc) > 1 else None
        if r.status != 200 or not isinstance(lst, list):
            raise YouTubeError(f"no suggestions from YouTube (HTTP "
                               f"{r.status})",
                               BAD_ANSWER if r.status == 200 else IO)
        return [text.clean(s) for s in lst if isinstance(s, str)][:10]

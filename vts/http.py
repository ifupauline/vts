# SPDX-License-Identifier: Apache-2.0
"""
HTTP, with what vts needs: size limits, retries of what may pass, no
redirection from https to http, compressed answers, several at once.
"""

import concurrent.futures
import http.client
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from vts import log

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 "
              "Firefox/140.0")
MAX_SIZE = 16 * 1024 * 1024
TIMEOUT = 30.0
CONNECT_TIMEOUT = 10.0
MAX_REDIRECTS = 5
RETRY_FIRST = 0.5
RETRY_MAX = 4.0

# kinds of failure, when no answer came
TIMEOUT_ERR = "timeout"
NOT_FOUND = "not-found"  # the name of the server
REFUSED = "refused"
RESET = "reset"
TLS = "tls"
TOO_BIG = "too-big"
LOOP = "loop"
OTHER = "other"


class Request:
    def __init__(self, url, body=None, content_type=None, cookie=None,
                 headers=None, max_size=0, timeout=0.0, retries=0):
        self.url = url
        self.body = body  # bytes: sent with POST
        self.content_type = content_type
        self.cookie = cookie
        self.headers = headers or {}
        self.max_size = max_size or MAX_SIZE
        self.timeout = timeout or TIMEOUT
        self.retries = retries  # more attempts after a failure that may pass


class Response:
    def __init__(self):
        self.status = 0  # the HTTP status, 0 if no answer came
        self.body = b""
        self.error = None  # one of the kinds above when no answer came
        self.message = ""  # what went wrong, for people

    def text(self):
        return self.body.decode("utf-8", "replace")


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url).scheme
        new = urllib.parse.urlsplit(newurl).scheme
        if new not in ("http", "https") or (old == "https" and new != "https"):
            raise urllib.error.HTTPError(newurl, code,
                                         "redirection refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = None


def _get_opener():
    global _opener
    if _opener is None:
        ctx = ssl.create_default_context()
        _opener = urllib.request.build_opener(
            _Redirects(), urllib.request.HTTPSHandler(context=ctx))
    return _opener


def _read(f, max_size, encoding):
    """the body of @f, uncompressed, at most @max_size bytes"""
    d = None
    if encoding == "gzip":
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        d = zlib.decompressobj()
    parts = []
    size = 0
    while True:
        chunk = f.read(65536)
        if not chunk:
            break
        if d:
            chunk = d.decompress(chunk, max_size + 1 - size)
            if d.unconsumed_tail:
                size = max_size + 1
        size += len(chunk)
        if size > max_size:
            raise _TooBig()
        parts.append(chunk)
    if d:
        rest = d.flush()
        size += len(rest)
        if size > max_size:
            raise _TooBig()
        parts.append(rest)
    return b"".join(parts)


class _TooBig(Exception):
    pass


def _failure(e):
    """(kind, message) of the exception @e"""
    reason = getattr(e, "reason", e)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return TIMEOUT_ERR, "timed out"
    if isinstance(reason, socket.gaierror):
        return NOT_FOUND, "the server's name was not found"
    if isinstance(reason, ConnectionRefusedError):
        return REFUSED, "connection refused"
    if isinstance(reason, ssl.SSLError):
        return TLS, f"secure connection failed: {reason.reason or reason}"
    if isinstance(reason, (ConnectionResetError, ConnectionAbortedError,
                           BrokenPipeError, http.client.HTTPException)):
        return RESET, "connection lost"
    if isinstance(reason, OSError) and reason.strerror:
        return OTHER, reason.strerror.lower()
    return OTHER, str(reason) or "failed"


def _may_pass(r):
    if r.error:
        return r.error in (TIMEOUT_ERR, NOT_FOUND, REFUSED, RESET, OTHER)
    return r.status in (429, 500, 502, 503, 504)


def _attempt(req):
    r = Response()
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    if req.body is not None and req.content_type:
        headers["Content-Type"] = req.content_type
    if req.cookie:
        headers["Cookie"] = req.cookie
    headers.update(req.headers)
    u = urllib.request.Request(req.url, data=req.body, headers=headers,
                               method="POST" if req.body is not None
                               else "GET")
    try:
        with _get_opener().open(u, timeout=req.timeout) as f:
            r.status = f.status
            r.body = _read(f, req.max_size,
                           f.headers.get("Content-Encoding", ""))
    except urllib.error.HTTPError as e:
        if e.msg == "redirection refused":
            r.error = OTHER
            r.message = "redirected somewhere insecure"
            return r
        if e.code in (301, 302, 303, 307, 308):
            r.error = LOOP
            r.message = "too many redirections"
            return r
        r.status = e.code
        try:
            r.body = _read(e, req.max_size,
                           e.headers.get("Content-Encoding", "")
                           if e.headers else "")
        except _TooBig:
            r.body = b""
        except (OSError, http.client.HTTPException, zlib.error):
            r.body = b""
        finally:
            e.close()
    except _TooBig:
        r.error = TOO_BIG
        r.message = f"answer larger than {req.max_size} bytes"
        return r
    except zlib.error:
        r.error = RESET
        r.message = "damaged answer"
        return r
    except (urllib.error.URLError, OSError, http.client.HTTPException,
            ValueError) as e:
        r.error, r.message = _failure(e)
        return r
    if r.status >= 400:
        r.message = f"HTTP {r.status}"
    return r


def fetch(req):
    """the answer to @req, after its retries; never raises"""
    return fetch_all([req])[0]


def fetch_all(reqs, parallel=1):
    """the answers to @reqs, at most @parallel at once"""
    resps = [None] * len(reqs)
    todo = list(range(len(reqs)))
    attempts = [0] * len(reqs)
    wait = RETRY_FIRST
    parallel = max(1, parallel)
    while todo:
        if parallel == 1 or len(todo) == 1:
            for i in todo:
                resps[i] = _attempt(reqs[i])
        else:
            with concurrent.futures.ThreadPoolExecutor(
                    min(parallel, len(todo))) as ex:
                for i, r in zip(todo, ex.map(lambda i: _attempt(reqs[i]),
                                             todo)):
                    resps[i] = r
        again = []
        for i in todo:
            attempts[i] += 1
            if _may_pass(resps[i]) and attempts[i] <= reqs[i].retries:
                again.append(i)
        todo = again
        if todo:
            log.dbg(f"http: {len(todo)} request(s) failed, again in "
                    f"{int(wait * 1000)} ms")
            time.sleep(wait)
            wait = min(wait * 2, RETRY_MAX)
    return resps


def escape(s):
    """@s for a URL's query"""
    return urllib.parse.quote(s, safe="-_.~")

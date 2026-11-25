#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Pretends to be Streamlink for the tests: writes its command line to
FAKE_LOG (JSON, as {"streamlink": argv}), then:

  --version             says "streamlink 8.6.3"
  a page of a channel named "offline..."
                        says it has no stream, as Streamlink does
  --stdout              writes a little of a "stream", then waits until
                        stopped (mpv reads it)
  --output FILE         writes to FILE a block every 0.2 s until stopped
"""
import json
import os
import sys
import time


def log(entry):
    path = os.environ.get("FAKE_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def main(argv):
    log({"streamlink": argv})
    if "--version" in argv:
        print("streamlink 8.6.3")
        return 0
    url = next((a for a in argv if a.startswith("https://")), "")
    if url.rsplit("/", 1)[-1].startswith("offline"):
        print(f"error: No playable streams found on this URL: {url}",
              file=sys.stderr)
        return 1
    if "--output" in argv:
        path = argv[argv.index("--output") + 1]
        with open(path, "wb") as f:
            while True:
                f.write(b"\x47" * 188 * 50)
                f.flush()
                time.sleep(0.2)
    if "--stdout" in argv:
        try:
            sys.stdout.buffer.write(b"\x47" * 188 * 10)
            sys.stdout.flush()
        except BrokenPipeError:
            return 0
        while True:
            time.sleep(0.2)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)

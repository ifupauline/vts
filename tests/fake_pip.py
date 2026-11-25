#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Pretends to be pip for the tests of vts updating itself: writes its
command line to FAKE_LOG (JSON, as {"pip": argv}), then fails with
"ERROR: no network" if FAKE_PIP_FAIL is in one of its arguments.
"""
import json
import os
import sys


def main(argv):
    path = os.environ.get("FAKE_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps({"pip": argv}) + "\n")
    fail = os.environ.get("FAKE_PIP_FAIL")
    if fail and any(fail in a for a in argv):
        print("Collecting something")
        print("ERROR: no network")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

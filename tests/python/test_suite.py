# SPDX-License-Identifier: Apache-2.0
"""
The whole-program checks of tests/test_cli.py (the commands on a
terminal, playback with the fake mpv, notifications, timers, the full
screen app on a pseudo terminal), each part a test here. They need a
Unix system: pseudo terminals, Unix sockets, symbolic links.
"""

import os
import stat
import sys

import pytest
from conftest import ROOT, TESTS, WINDOWS

pytestmark = pytest.mark.skipif(WINDOWS, reason="needs a Unix system")

PARTS = ("test_version", "test_help", "test_bad_usage", "test_config",
         "test_search", "test_play", "test_check", "test_tui",
         "test_thumbnails", "test_update")


@pytest.fixture(scope="module")
def program(tmp_path_factory):
    """vts as one program to run, as the old tests want it"""
    d = tmp_path_factory.mktemp("bin")
    path = d / "vts"
    path.write_text(f"#!/bin/sh\nVTS_PROGRAM=\"$0\" PYTHONPATH={ROOT} "
                    f"exec {sys.executable} -m vts \"$@\"\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture(scope="module")
def suite():
    sys.path.insert(0, TESTS)
    import test_cli
    return test_cli


@pytest.mark.parametrize("part", PARTS)
def test_part(part, program, suite, tmp_path):
    from vts import __version__
    before = suite.failures
    f = getattr(suite, part)
    if part in ("test_version", "test_help", "test_bad_usage"):
        f(program, __version__)
    else:
        if part in ("test_tui", "test_thumbnails"):
            pytest.importorskip("pyte")
        f(program, str(tmp_path))
    assert suite.failures == before, f"{suite.failures - before} check(s) " \
        "failed: see the CHECK lines above"


def test_no_leftovers():
    assert not os.path.exists(os.path.join(TESTS, "port"))

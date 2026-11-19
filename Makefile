# SPDX-License-Identifier: Apache-2.0
#
# Shortcuts for working on vts (nothing to build: it is Python).
#
#   make test         run the tests (pytest; pyte for the full screen app)
#   make lint         check the code with ruff
#   make live-test    check vts against the real YouTube (needs internet)
#   make fuzz         damaged and hostile answers, ROUNDS=2000 for more
#   make screenshots  the pictures of the README (needs pyte)
#   make install      install vts for you with pipx, from this folder
#   make clean        remove what the tests and Python left behind

PYTHON ?= python3
RUN    := build/vts

.PHONY: all test lint live-test fuzz screenshots install clean

all: test

# one program to run, for the scripts that want one
$(RUN): Makefile
	@mkdir -p build
	@printf '#!/bin/sh\nVTS_PROGRAM="$$0" PYTHONPATH="%s" exec %s -m vts "$$@"\n' \
		"$(CURDIR)" "$(PYTHON)" > $@
	@chmod +x $@

test:
	$(PYTHON) -m pytest -q tests/python

lint:
	ruff check vts tests

# exit status 3: YouTube refused this network for a while, try later
live-test: $(RUN)
	$(PYTHON) tests/live_test.py $(RUN)

# failures are kept in fuzz-crash-N/
fuzz: $(RUN)
	$(PYTHON) tests/fuzz.py $(RUN) $(or $(ROUNDS),500)

screenshots: $(RUN)
	$(PYTHON) doc/screenshots/shoot.py $(RUN)

install:
	pipx install --force .

clean:
	rm -rf build fuzz-crash-* .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

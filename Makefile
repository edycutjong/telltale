# Telltale top-level Makefile.
#   make test-host   host-runnable unit tests (any POSIX cc + Python 3.11+), also run in CI
#   make lint        ruff over the host-side Python
#   make target      QNX 8.0 binaries via qcc - needs a sourced QNX SDP 8.0 (qnxsdp-env.sh)

CC      ?= cc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -O2
PYTHON  ?= python3
HOSTBIN := tests/host/bin

.PHONY: all test-host test-host-c test-host-py lint target clean

all: test-host

$(HOSTBIN):
	mkdir -p $@

$(HOSTBIN)/test_disp: tests/host/test_disp.c src/common/disp.c src/common/disp.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_disp.c src/common/disp.c -lm

test-host-c: $(HOSTBIN)/test_disp
	$(HOSTBIN)/test_disp

test-host-py:
	$(PYTHON) -m unittest discover -s tests/host -p 'test_*.py' -v

test-host: test-host-c test-host-py

lint:
	ruff check scripts tests

target:
	@command -v qcc >/dev/null 2>&1 || { echo "qcc not found: source the QNX SDP 8.0 qnxsdp-env.sh first"; exit 1; }
	@echo "no target sources yet"; exit 1

clean:
	rm -rf $(HOSTBIN)

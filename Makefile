# Telltale top-level Makefile.
#   make test-host   host-runnable unit tests (any POSIX cc + Python 3.11+), also run in CI
#   make lint        ruff over the host-side Python
#   make target      QNX 8.0 binaries via qcc (+ the compiled spike policy) into out/$(QNX_ARCH)/;
#                    uses qcc from PATH (sourced qnxsdp-env.sh) or else scripts/qcc.sh (Docker host-tools image)
#   make spike       run the P1a gate spikes (G1-G4) on the running QEMU target -> evidence/spike/

CC      ?= cc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -O2
PYTHON  ?= python3
HOSTBIN := tests/host/bin

QNX_ARCH ?= x86_64
QOUT     := out/$(QNX_ARCH)
QCC      := qcc -Vgcc_nto$(QNX_ARCH)
QCFLAGS  := -std=gnu11 -O2 -Wall -Wextra -Werror
SPIKES   := $(QOUT)/g1_readback $(QOUT)/g2_hello $(QOUT)/g3_secpol $(QOUT)/g4_timer $(QOUT)/g3_spike.bin

.PHONY: all test-host test-host-c test-host-py lint target target-sdp spike clean

all: test-host

$(HOSTBIN):
	mkdir -p $@

$(HOSTBIN)/test_disp: tests/host/test_disp.c src/common/disp.c src/common/disp.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_disp.c src/common/disp.c -lm

COMMON_SRC := src/common/decode.c src/common/truth.c src/common/disp.c

$(HOSTBIN)/test_decode: tests/host/test_decode.c $(COMMON_SRC) src/common/*.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_decode.c $(COMMON_SRC) -lm

test-host-c: $(HOSTBIN)/test_disp $(HOSTBIN)/test_decode
	$(HOSTBIN)/test_disp
	$(HOSTBIN)/test_decode

test-host-py:
	$(PYTHON) -m unittest discover -s tests/host -p 'test_*.py' -v

test-host: test-host-c test-host-py

lint:
	ruff check scripts tests tools

target:
	@if command -v qcc >/dev/null 2>&1; then $(MAKE) target-sdp; \
	else scripts/qcc.sh 'make target-sdp QNX_ARCH=$(QNX_ARCH)'; fi

target-sdp: $(SPIKES)

$(QOUT):
	mkdir -p $@

$(QOUT)/g1_readback: spike/g1_readback.c | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $< -lscreen

$(QOUT)/g2_hello: spike/g2_hello.c | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $<

$(QOUT)/g3_secpol: spike/g3_secpol.c | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $< -lsecpol

$(QOUT)/g4_timer: spike/g4_timer.c | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $<

$(QOUT)/g3_spike.bin: spike/g3_spike.pol | $(QOUT)
	secpolcompile -o $@ $<

spike: target
	scripts/spike.sh

clean:
	rm -rf $(HOSTBIN) out

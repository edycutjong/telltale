# Telltale top-level Makefile.
#   make test-host   host-runnable unit tests (any POSIX cc + Python 3.11+), also run in CI
#   make lint        ruff over the host-side Python
#   make target      QNX 8.0 binaries via qcc (+ the compiled spike policy) into out/$(QNX_ARCH)/;
#                    uses qcc from PATH (sourced qnxsdp-env.sh) or else scripts/qcc.sh (Docker host-tools image)
#   make test-target the on-target suite on the running QEMU guest (scripts/test_target.sh; needs make target + deploy)
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
TT_BINS  := $(QOUT)/vehicled $(QOUT)/vehfeed $(QOUT)/cluster $(QOUT)/telltale $(QOUT)/ttinject $(QOUT)/ttctl $(QOUT)/hog $(QOUT)/ttshot $(QOUT)/ttrec $(QOUT)/telltale.bin
QCOMMON  := src/common/ttsec.c src/common/disp.c
QHDRS    := $(wildcard src/common/*.h)

.PHONY: all test-host test-host-c test-host-py lint target target-sdp spike clean test-target

all: test-host

$(HOSTBIN):
	mkdir -p $@

$(HOSTBIN)/test_disp: tests/host/test_disp.c src/common/disp.c src/common/disp.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_disp.c src/common/disp.c -lm

COMMON_SRC := src/common/decode.c src/common/truth.c src/common/disp.c

$(HOSTBIN)/test_decode: tests/host/test_decode.c $(COMMON_SRC) src/common/*.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_decode.c $(COMMON_SRC) -lm

$(HOSTBIN)/test_text: tests/host/test_text.c src/common/text.c src/common/text.h src/common/textfont.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_text.c src/common/text.c

$(HOSTBIN)/test_rcap: tests/host/test_rcap.c src/common/rcap.h | $(HOSTBIN)
	$(CC) $(CFLAGS) -o $@ tests/host/test_rcap.c

test-host-c: $(HOSTBIN)/test_disp $(HOSTBIN)/test_decode $(HOSTBIN)/test_text $(HOSTBIN)/test_rcap
	$(HOSTBIN)/test_disp
	$(HOSTBIN)/test_decode
	$(HOSTBIN)/test_text
	$(HOSTBIN)/test_rcap

test-host-py:
	$(PYTHON) -m unittest discover -s tests/host -p 'test_*.py' -v

test-host: test-host-c test-host-py

test-target:
	scripts/test_target.sh

lint:
	ruff check scripts tests tools

target:
	@if command -v qcc >/dev/null 2>&1; then $(MAKE) target-sdp; \
	else scripts/qcc.sh 'make target-sdp QNX_ARCH=$(QNX_ARCH)'; fi

target-sdp: $(SPIKES) $(TT_BINS)

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

$(QOUT)/vehicled: src/vehicled/vehicled.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ src/vehicled/vehicled.c $(QCOMMON) -lsecpol

$(QOUT)/vehfeed: src/vehfeed/vehfeed.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ src/vehfeed/vehfeed.c $(QCOMMON) -lsecpol -lm

$(QOUT)/cluster: src/cluster/cluster.c src/common/decode.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ src/cluster/cluster.c src/common/decode.c $(QCOMMON) -lscreen -lsecpol -lm

$(QOUT)/telltale: src/telltale/telltale.c src/common/decode.c src/common/truth.c src/common/text.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ src/telltale/telltale.c src/common/decode.c src/common/truth.c src/common/text.c $(QCOMMON) -lscreen -lsecpol -lslog2 -lm

$(QOUT)/ttinject $(QOUT)/ttctl $(QOUT)/hog: $(QOUT)/%: src/tools/%.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $< $(QCOMMON) -lsecpol

$(QOUT)/ttshot $(QOUT)/ttrec: $(QOUT)/%: src/tools/%.c $(QCOMMON) $(QHDRS) | $(QOUT)
	$(QCC) $(QCFLAGS) -o $@ $< $(QCOMMON) -lscreen -lsecpol

$(QOUT)/telltale.bin: secpol/telltale.pol | $(QOUT)
	secpolcompile -o $@ $<

spike: target
	scripts/spike.sh

clean:
	rm -rf $(HOSTBIN) out

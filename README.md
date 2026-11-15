# Telltale

Telltale is being built as an isolated QNX 8.0 monitor that reads back what the instrument cluster actually shows and checks it against the vehicle state.

**Status: early build.** This repository is being written during the QNX hackathon (Nov–Dec 2026). The product code
(resource manager, Screen renderer, monitor, fault injector) has not landed yet. What runs on the QNX 8.0 target today
is the set of gate spikes that the design depends on: Screen pixel read-back, timing, and secpol types. See
[`evidence/spike/`](evidence/spike/README.md). Every time there is QEMU (TCG), not hardware timing.

## What is here today
| Path | What |
|---|---|
| `src/common/` | shared C code (`vehicle.h`, `disp.h`, `disp.c`): vehicle state layout, the `disp_kmh()` rounding rule the renderer and the monitor both use |
| `scripts/bench.py` | host-side analysis of bench CSVs from the target: per-class p50/p95/max, misses, failed rows, false positives |
| `tools/fetch_comma_speed.py` | pulls only the CAN speed arrays out of a ~9 GB comma2k19 chunk with HTTP range reads (about 16 MB of extracted arrays for Chunk_1 plus one Chunk_8 route, measured with `du` outside the repo; each zip's directory is read too) |
| `tools/trace_from_comma.py` | builds the real speed traces: the hero stop, the 31-min bench drive, the speed-changing-window census |
| `data/` | the derived traces (real comma2k19 CAN samples, MIT) with provenance and SHA-256 in [`data/SOURCES.md`](data/SOURCES.md) |
| `spike/` | the QNX 8.0 gate spikes (C, `qcc`): Screen read-back of the composed display and of a window (G1), a 20 ms timer-pulse jitter probe (G4), secpol types and channel-connect rules (G3) |
| `evidence/spike/` | one recorded spike run on the QEMU target: raw output, read-back images, `SUMMARY.md` |
| `scripts/run_qemu.sh` | boots the QNX Everywhere Quick Start image for QEMU (x86_64 guest) on a macOS host: TCG, user-mode net, serial socket |
| `scripts/qcc.sh`, `docker/` | runs the QNX SDP 8.0 Linux host tools (`qcc`, `secpolcompile`) in a linux/amd64 container; the SDP stays on the host |
| `scripts/guest.sh`, `tools/qnx_serial.py`, `scripts/spike.sh` | ssh/serial helpers for the guest and the spike runner (`make spike`) |
| `tests/host/` | host-runnable unit tests (C + Python); `tests/fixtures/synthetic-*.csv` are hand-written test inputs, not measurements |

## Run the host tests
```sh
make test-host   # C tests with any POSIX cc, Python tests with Python 3.11+
make lint        # ruff
```

## Build and run on the QNX target
```sh
docker build --platform linux/amd64 -t telltale-qnx-host:local docker
make target        # qcc -> out/x86_64/ (needs QNX SDP 8.0 installed on the host, default ~/qnx800)
make spike         # with the target booted by scripts/run_qemu.sh; see evidence/spike/README.md
```

## License
Apache-2.0, see [LICENSE](LICENSE).

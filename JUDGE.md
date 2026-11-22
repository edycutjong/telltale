# Telltale: a 2-minute guide for judges

**What it is.** A separate QNX 8.0 process that reads back the pixels an instrument cluster actually shows (QNX Screen), decodes the speed digits, 6 lamps and a liveness tick, and checks them against the vehicle state served by a resource manager. When the cluster lies, for example frozen at 48 km/h while the car has stopped, it puts a safe layer with the true speed on screen, deals with the renderer, and hands the display back only after a verified true frame.

**Why it matters.** A watchdog only proves the renderer is alive, and a self-report only says what it meant to draw. In the bench, a tick-only watchdog would not have caught the 50 live-but-stale renderers (by construction: STALE keeps its tick alive), and a self-report check would have passed all 50 BLANK and 10 MISDRAW runs. Reading the glass back caught every one.

All timings below are **QEMU (TCG) on an x86_64 guest, not hardware timing.**

## The four numbers

| Claim | Evidence |
|---|---|
| 200 injected faults in 4 classes, **0 missed** (bench built at `7592e40`; regression re-checked 25/25 at the current monitor `f2b8002`) | [`evidence/RESULTS.md`](evidence/RESULTS.md), [DEMO.md](DEMO.md) |
| **0 false positives** in 91,029 checks (first 30 minutes of a real 31-minute drive) | [`evidence/RESULTS.md`](evidence/RESULTS.md) |
| Monitor off: wrong glass for a median **29.9 s** of a 30 s window | [DEMO.md](DEMO.md) |
| On-target suite **190 passed / 0 failed** at `f2b8002` | [`evidence/test-target/20261122T172933Z/summary.txt`](evidence/test-target/20261122T172933Z/summary.txt) |

Not every target was met. The <100 ms stretch target was met only for BLANK, and STALE missed its W+100 ms target. Both misses are published in [DEMO.md](DEMO.md).

## The video, by chapter (2:51): [youtu.be/ONV3rW4BZKo](https://youtu.be/ONV3rW4BZKo)

| Time | What to watch |
|---|---|
| 0:00 | The frozen speedometer: real QNX footage, the lie and the catch |
| 0:21 | What a heartbeat and a self-report miss |
| 0:34 / 0:54 | Stale (alive, ticking, wrong) and Misdraw (48 drawn as 84) |
| 1:07 | How it works: plain QNX primitives |
| 1:37 | Security policy, then a crash restart |
| 1:56 | Monitor off: the lie stays |
| 2:10 | The bench: 200 faults, 0 missed |
| 2:35 | Limits and links |

## QNX-specific technology, and where it lives

| Primitive | Use | Source |
|---|---|---|
| Resource managers | `/dev/vehicle` (truth), `/dev/telltale`, `/dev/telltale-events` | [`src/vehicled/vehicled.c`](src/vehicled/vehicled.c), [`src/telltale/telltale.c`](src/telltale/telltale.c) |
| Message passing | devctl, pulses, `ionotify` on the renderer's data path | [`src/vehicled/vehicled.c`](src/vehicled/vehicled.c), [`src/cluster/cluster.c`](src/cluster/cluster.c) |
| Real-time | `SCHED_FIFO` 60 monitor, woken by a `timer_create(SIGEV_PULSE)` pulse every 20 ms (configured period) | [`src/telltale/telltale.c`](src/telltale/telltale.c) |
| QNX Screen | `screen_read_display` read-back, `screen_post_window` safe layer, `screen_read_window` before hand-back | [`src/telltale/telltale.c`](src/telltale/telltale.c), [`src/common/handback.h`](src/common/handback.h) |
| `secpol` | one type each for feeder, vehicled, renderer and monitor; enforced in the on-target suite (22 checks, 13 denials), not loaded during the timing bench | [`secpol/telltale.pol`](secpol/telltale.pol) |
| `slog2`, `slm` | incident records; `slm` can supervise the renderer (on-target `slm-freeze` test) | [`src/telltale/telltale.c`](src/telltale/telltale.c) (slog2), [`scripts/target/telltale-slm.xml`](scripts/target/telltale-slm.xml) (slm) |

## Check the claims without a QNX licence

```sh
make test-host                       # C + Python host tests
scripts/replay_target_run.sh         # re-checks every recorded assertion of the cited on-target run
scripts/bench_all.sh report && git diff --exit-code evidence/   # bench tables regenerate byte-identically
```
The replay needs a full clone (it diffs against the cited commit). CI runs the same checks on every push: `make test-host` split into its Python half (Stage 1) and C half (Stage 3), `scripts/replay_target_run.sh` (Stage 4), and `scripts/bench_all.sh report` with a diff on `evidence/RESULTS.md` and `report.html` (Stage 5) ([CI/CD Pipeline](https://github.com/edycutjong/telltale/actions/workflows/ci.yml), 6 stages). Building and running on QNX needs SDP 8.0 and the QNX Everywhere QEMU image: see [Getting Started](README.md#-getting-started).

## Limits, stated up front
Not certified, and no ISO 26262 or ASIL claim. Timing is QEMU (TCG), not hardware. The monitor itself is not supervised. Gear is drawn but not verified. Whole-CPU starvation delays the safe layer until the hog ends. The 200 injections are a declared population in one real drive, not general coverage. Full list: [`docs/ENGINEERING.md`](docs/ENGINEERING.md#known-limits-measured).

## Also submitted
- **Codelab**, "Your first QNX resource manager on an Apple-Silicon Mac": [telltale.edycu.dev/codelab](https://telltale.edycu.dev/codelab/) ([source](codelab/README.md))
- **Site**: [telltale.edycu.dev](https://telltale.edycu.dev) · **Pitch deck**: [telltale.edycu.dev/pitch](https://telltale.edycu.dev/pitch/)

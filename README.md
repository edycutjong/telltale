# Telltale

Telltale is an isolated QNX 8.0 monitor that reads back what the instrument cluster actually shows and checks it against the vehicle state.

**Status: core flow running on the QNX 8.0 target (QEMU).** This repository is being written during the QNX
hackathon (Nov–Dec 2026). The whole loop runs on a QNX 8.0 x86_64 guest under QEMU (TCG): `vehicled` serves the
vehicle state, `cluster` draws it with QNX Screen, and `telltale` reads the composed display back every 20 ms, decodes
the digits and lamps, and checks them against `/dev/vehicle`. On a lie it puts a safe layer with the true values on
top, decides hung vs starved from the renderer's thread states, kills and respawns a hung or wrong renderer, verifies
the new one, and hands the display back. 20 ms is the configured check period (`period_ms`), not a measured figure.
Recorded runs per fault class: [`evidence/runs/`](evidence/runs/). Tests: `make test-host` = C checks (test_disp 12, test_decode 91,
test_text 17, test_rcap 28) + 97 Python tests (8 need Pillow). `make test-target`: 155 passed, 0 failed on the QEMU (TCG) guest at
commit 1989412 ([`evidence/test-target/20261115T213814Z/summary.txt`](evidence/test-target/20261115T213814Z/summary.txt), host
load logged at start and end), including `tests/target/core_items.sh` 37/37 (`/dev/vehicle`, `ttctl`, `/dev/telltale`,
TRUTH_LOST + recovery, DISARM mid-incident, a vehicled restart, NO_DISPLAY, startup without truth, the restart cap, a crash
after earlier incidents, a monitor restart, the incident in slog2), the secpol denials 21/21, and the engineering-mode case whose banner
and incident card (and the card gone after `card_ms`) are read back from frames a separate process captured.
Two failing runs are kept: [`20261115T141317Z`](evidence/test-target/20261115T141317Z/summary.txt) (110 passed, 1 failed) caught a
race where a frozen renderer was killed as "live-wrong" before its stall was classified, and
[`20261115T204528Z`](evidence/test-target/20261115T204528Z/summary.txt) (148 passed, 5 failed) caught a renderer that was blank
while its tick had stopped being held forever instead of killed (both fixed since).

## Bench results (QEMU TCG, raw rows in `evidence/`)
**On QEMU (TCG), p95 detection was 24.8 ms (BLANK) to 271 ms (STALE); the pre-registered 197 ms formula bound is met for BLANK, FREEZE and STARVE, the < 100 ms stretch target only for BLANK, and STALE missed its W + 100 ms target. All 200 injected faults were detected, 0 missed; the monitor-off ablation showed a wrong or missing speed for about 29.9 of 30 s; the 30-minute clean replay had 0 false positives in 91,029 checks.**
Every timing here is QEMU (TCG), not hardware timing. Tables: [`evidence/RESULTS.md`](evidence/RESULTS.md); plots: [`evidence/report.html`](evidence/report.html);
reproduce: `scripts/bench_all.sh run` then `report`. The thresholds were calibrated on a clean run ([`evidence/calibration/`](evidence/calibration/): 600 s, `tick_stall_ms` raised to 1000 for that run only so the stall check could not fire,
`tick_stall_ms` = 1.5 x the largest renderer frame gap = 151) and committed before the first fault row.

| class (50 injections each) | misses | detect p50 / p95 (ms) | stretch target < 100 ms (p95) | formula bound 197 ms (p95) |
|---|---|---|---|---|
| BLANK | 0 | 17.5 / 24.8 | met | met |
| FREEZE | 0 | 106.8 / 129.8 | missed | met |
| STARVE | 0 | 147.6 / 175.8 | missed | met |
| STALE | 0 | 168.2 / 271.2 | n/a: W + 100 = 200 ms target **missed** | n/a (value-detected) |

The formula bound and the < 100 ms stretch target were written down in the project's private specs on 2026-11-13, before any run (this public history first contains them after the B1 run); the bound is `tick_stall_ms` 151 + period 20 + read-back p95 6.3 + 20 ms. Detection time is dominated by the configured `tick_stall_ms`, not by monitor speed: the period sweep (FREEZE x 50 per period; the 20 ms point is the B1 FREEZE set) gives detect p50 / p95 of 100 / 122 ms at 10 ms and 107 / 157 ms at 50 ms, while the safe layer appears p50 26 ms (20 ms period) to 49 ms (50 ms period) after detection.
STALE needs the true speed to move, so it carries the 100 ms truth window by construction; its p95 overshoots the 200 ms target by about 70 ms, which this bench does not decompose, and the target was not tuned afterwards. All 50 STALE rows were first caught by the value check (`VALUE_MISMATCH`), none by a tick stall: a tick-only watchdog would not have caught STALE, by construction (STALE keeps the tick alive), shown as a control rather than a detection result. Safe layer on screen p50 22.5-30.1 ms after detection; renderer restarted and verified true p50 281-338 ms after detection (STARVE: when the 3 s hog ends).

- **Declared fault population, not general coverage.** The 200 injections are placed in the 21 speed-changing windows of one real 31-minute comma2k19 drive, always 0.2 s into the window and one fresh stack per injection (so they are not 200 independent samples). STALE is injected only in the 11 windows where the true speed moves at least 2 km/h within a second; the first attempt put a STALE at 0 km/h (unobservable by construction), was stopped, and the rule was added afterwards (it supplements the census rule, which still gives 21 windows; see [`evidence/bench-discarded/`](evidence/bench-discarded/README.md)).
- **Monitor-off ablation** (40 runs, 10 per class, 30 s window, `ttctl disarm`: the monitor still reads but never acts): median 29.9 s of the window wrong (minimum 29.09 s; target: median >= 25 s, 0 runs below), still wrong at the end of all 40. A disarmed monitor cannot recover, so this is a control showing what the glass does without it, not a detection result.
- **30-minute clean replay (the first 1800 s of the same drive, 1820.8 s measured): 0 false positives** (target <= 1) in 91,029 armed checks (121 pre-arm warm-up rows are excluded and counted in the CSV header); the three-region read-back costs p50 / p95 5.7 / 6.3 ms per check.
- Also measured: 50 renderer crashes (detect p50 14 ms, 0 missed); 10 MISDRAW (the renderer intends the right value and draws another; 10/10 caught). Computed offline from the renderer's own intent log (within 1 km/h), a self-report check (intended value vs truth) would have passed all 50 BLANK and 10 MISDRAW runs; the pixel read caught every one. No self-report baseline is scored for FREEZE, STALE or STARVE.
- Conditions: the bench driver runs on the Mac and the stamps are written on the target; the runmask column reads `0xf` for every row although STARVE runs pin the renderer and the hog to one CPU; host load1 during the fault, ablation, crash and period runs was 1.8-35.9 and is logged per row. Every bench run had no security policy loaded (`TT_POLICY` unset: processes untyped, uid checks in code); the policy is exercised by `make test-target` (a FREEZE run with every process in its secpol type, plus the denial checks). The rows' `exposure_ms` column reads 0.0 because the monitor version that produced them (each row's `git_sha`) did not compute it; later versions write last verified frame -> truth on glass (see the regression check below).
- Regression check after the code review, 5 injections per class at commit b896938 ([`evidence/bench-regress-r03.csv`](evidence/bench-regress-r03.csv), [`evidence/bench-regress-r03-crash.csv`](evidence/bench-regress-r03-crash.csv)): 25/25 detected, 0 missed; detect p95 BLANK 21.9, FREEZE 118.5, STARVE 175.7, STALE 259.7, CRASH 18.8 ms; `exposure_ms` (last verified frame -> truth on glass) 35.8-50.1 ms for BLANK, FREEZE, STALE and STARVE, 203.9-251.8 ms for CRASH. Five per class checks that nothing regressed; it does not replace the 50-per-class tables above.
- Not run: the B7 holdout variants other than MISDRAW (external SIGSTOP of the renderer or Screen, random-time and steady-cruise injections, an independent fixture renderer) and monitor CPU % per period.

## What is here today
| Path | What |
|---|---|
| `src/vehicled/` | the `/dev/vehicle` resource manager: `cat /dev/vehicle`, devctl GET / GET_SINCE / SET, ionotify pulses; writes only from the feeder (secpol `vehicle:write`) |
| `src/vehfeed/` | replays a real comma2k19 speed trace plus the scripted lamp scenario (`data/scenario-hero.csv`) into `/dev/vehicle` in real time |
| `src/cluster/` | the instrument cluster: a QNX Screen renderer (speed digits from the D-DIN glyph atlas, gear strip (drawn, not verified by the monitor), 6 lamps, a 4-bit liveness tick) with test hooks on `/dev/cluster-ctl` |
| `src/telltale/` | the monitor: SCHED_FIFO 60, 20 ms timer pulse, `screen_read_display` of the composed digits, lamps and tick, `screen_read_window` of the renderer's own window, truth window, verdicts, safe layer, cause check via `/proc`, kill / respawn / verified hand-back, incidents on `/dev/telltale-events`; the fault banner (driver mode: `DISPLAY FAULT · SHOWING TRUE SPEED`, or `· SPEED DATA LOST` when the truth is lost, no metrics; engineering mode adds the verdict name, e.g. `VALUE_MISMATCH`, and this incident's caught ms) and, in engineering mode, an incident card in its own window for `card_ms` (default 5000) after recovery (id, verdict (+ cause), shown vs truth, caught / restored ms — both measured from the last verified frame — action, `cat /dev/telltale`) |
| `src/tools/` | `ttinject` (FREEZE lock inversion, BLANK, STALE, MISDRAW, CRASH, LAMPDROP, heal), `hog` (the STARVE fault: an external priority hog), `ttctl` (arm / disarm / rearm / period / `debug on` = engineering mode), `ttshot` (display capture to PPM) |
| `src/common/` | shared C code: vehicle state and devctl protocol, layout and palette, glyph atlas (`glyphs.h`, generated by `tools/gen_glyphs.py`), digit + lamp decoder, truth window, `disp_kmh()` rounding rule, secpol entry (`ttsec.c`) |
| `secpol/telltale.pol` | the security policy: one type per process, channel-connect rules, `vehicle:write` for the feeder only, `telltale:control` for the injector only, the monitor's signal / process-query abilities, the hog capped at priority 40 |
| `scripts/scenario.sh`, `scripts/target/` | one recorded run on the target (start the stack, fire a fault at a trace time, collect the logs); `scripts/target/tt_up.sh` is the start script (vehicled → telltale → cluster → vehfeed); the renderer is restarted by a shell loop or, with `TT_SUP=slm`, by QNX `slm` (`telltale-slm.xml`) |
| `scripts/test_target.sh`, `scripts/check_run.py`, `tests/target/` | the on-target test suite (`make test-target`): one run per fault class, a clean drive, the hold → heal → hand-back sequence, the same in engineering mode (banner + card read back), `tests/target/core_items.sh` (`/dev/vehicle`, `ttctl`, `/dev/telltale`, TRUTH_LOST and its recovery, DISARM, vehicled and monitor restarts, NO_DISPLAY, the restart cap), secpol denials |
| `scripts/bench.py`, `scripts/bench_run.py`, `scripts/bench_all.sh`, `scripts/bench_report.py`, `scripts/calibrate.py` | the bench: `bench_run.py` schedules one fresh stack per injection on the guest and turns the target's own stamps into rows; `bench.py` analyses them (per-class p50/p95/max, misses, failed rows, false positives, ablation); `bench_all.sh` reproduces everything and writes `evidence/RESULTS.md`; `bench_report.py` writes `evidence/report.html`; `calibrate.py` derives `tick_stall_ms` from a clean run |
| `tools/fetch_comma_speed.py` | pulls only the CAN speed arrays out of a comma2k19 chunk with HTTP range reads (each zip's central directory is read too) |
| `tools/trace_from_comma.py` | builds the real speed traces: the hero stop, the 31-min bench drive, the speed-changing-window census |
| `data/` | the derived traces (real comma2k19 CAN samples, MIT) with provenance and SHA-256 in [`data/SOURCES.md`](data/SOURCES.md) |
| `spike/` | the QNX 8.0 gate spikes (C, `qcc`): Screen read-back of the composed display and of a window (G1), a 20 ms timer-pulse jitter probe (G4), secpol types and channel-connect rules (G3) |
| `evidence/spike/` | one recorded spike run on the QEMU target: raw output, read-back images, `SUMMARY.md` |
| `scripts/run_qemu.sh` | boots the QNX Everywhere Quick Start image for QEMU (x86_64 guest) on a macOS host: TCG, user-mode net, serial socket |
| `scripts/qcc.sh`, `docker/` | runs the QNX SDP 8.0 Linux host tools (`qcc`, `secpolcompile`) in a linux/amd64 container; the SDP stays on the host |
| `scripts/guest.sh`, `tools/qnx_serial.py`, `scripts/spike.sh` | ssh/serial helpers for the guest and the spike runner (`make spike`) |
| `tests/host/` | host-runnable unit tests (C + Python); `tests/fixtures/synthetic-*.csv` are hand-written test inputs, not measurements |

## Known limits (measured)
- **Whole-CPU starvation:** with Screen, the monitor, the renderer and the hog all on one CPU, Telltale detects the stall
  but its safe layer only becomes visible when the hog ends, because Screen's own threads (priority 10–16) are starved too
  ([`evidence/runs/p1b-130521-starve/`](evidence/runs/p1b-130521-starve/)); that one-CPU setup also overloads on TCG
  without the hog: 55 incidents in the run, 2 before the hog, 1 during it, 52 after it ended. STARVE is therefore shown and tested as
  **"STARVE (renderer CPU only)"**: renderer + hog pinned to one CPU, Screen and monitor free
  ([`evidence/runs/p1b-130644-starve/`](evidence/runs/p1b-130644-starve/)). No priority-inheritance claim is made.
- **Gear** is drawn but not verified. Speed digits, the 6 lamps and the liveness tick are verified.
- **Fixed-position cluster:** the monitor reads the renderer window's position once, when it finds the window (by id and
  owner pid), and composes its read rectangles from it; a renderer that moves its window afterwards is not followed.
- **secpol** restricts who writes `/dev/vehicle`, who controls and registers with the monitor, channel connects and the
  hog's priority; the monitor's `signal` ability is not target-scoped by the policy (targeting the renderer is enforced in
  code), and reading Screen buffers is a Screen permission, not a secpol rule. There is no `screen_t`: Screen is started
  before the runtime policy push (the push is immutable until reboot).
- `tick_stall_ms=151` is calibrated from one clean run on QEMU (TCG) (the Mac's load1 was about 25-70 during that run, noted in `scripts/target/telltale.conf`, i.e. heavier than during the fault runs, which only makes the threshold more conservative); `W_ms=100` and `restart_timeout_ms=4000` were kept, not tuned.
- Not asserted by a test yet: the restart cap on the no-window kill path (the test covers the main kill path: a renderer
  blank on every start is killed 3 times, then TT-GIVEUP, then rearm -> NORMAL), the kill after an UNKNOWN-cause stall
  (counted from when the cause became UNKNOWN, so one mixed state sample in a long STARVE cannot kill), the restart when a
  live renderer's window stays unreadable during SAFE-HOLD, and a renderer that is starved first and then hangs. `DCMD_VEH_GET_SINCE`
  (the monitor's truth path) and the ionotify pulses (the renderer's data path) run in every recorded run but have no
  separate assertion.
- **The monitor is not supervised:** nothing restarts `telltale` if it dies (the display keeps whatever the renderer draws);
  a restarted monitor relearns the renderer and arms again (asserted by `core_items.sh`). The monitor holds the
  `xprocess_debug` ability (`secpol/telltale.pol`) to read the renderer's thread states through `/proc` for the
  hung-vs-starved decision.
- The fault banner shows the monitor's verdict (`VALUE_MISMATCH`, `TICK_STALL`, ...), not the injected fault class: the
  monitor cannot know what was injected.

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
scripts/guest_screen.sh            # start Screen on the guest (software virtual display)
scripts/deploy.sh                  # copy binaries, policy, traces to /tmp/tt on the guest
scripts/scenario.sh freeze         # one recorded FREEZE run -> evidence/runs/<id>-freeze/
make test-target                   # the on-target suite -> evidence/test-target/<stamp>/summary.txt
```

## License
Apache-2.0, see [LICENSE](LICENSE).

# Telltale — demo and bench

Every timing on this page is **QEMU (TCG) on an x86_64 QNX 8.0 guest, not hardware timing**. Raw rows are in [`evidence/`](evidence/); generated tables in [`evidence/RESULTS.md`](evidence/RESULTS.md); plots in [`evidence/report.html`](evidence/report.html).

## The demo (one real stop)
The input is a real stop from the comma2k19 dataset: 49.01 km/h to 0 in 4.848 s ([`data/SOURCES.md`](data/SOURCES.md)). A STALE fault makes the cluster keep drawing a speed while the car stops. Without the monitor the glass keeps the old value for the rest of the window; with it, `telltale` reads the digits back, sees the mismatch, posts the true value and restores a verified renderer. One recorded run per fault class: [`evidence/runs/`](evidence/runs/) (`incidents.csv`, `checks.csv`, `telltale.log` per run). The animated hero in the README draws the same drive. The monitor-on run on this stop is [`evidence/runs/p1b-125318-stale`](evidence/runs/p1b-125318-stale/); the landing page and deck replay incident TT-FRZ-0001, a FREEZE injected on the same stop and recorded by `make test-target` at 985ab29, the monitor before the final-review fixes ([`evidence/test-target/20261115T232225Z/t-freeze/`](evidence/test-target/20261115T232225Z/t-freeze/)); the monitor-off figure below is the 40-run ablation on the bench drive, which includes this stop. The demo video's FREEZE shots are a separate recorded take on the same stop and monitor code ([`evidence/video/20261121T124518Z-freeze`](evidence/video/20261121T124518Z-freeze/)); incident ids restart at 0001 in every run, so it is also TT-FRZ-0001, with its own timings: caught 22.1 ms after the last verified frame (123.4 ms after the fault), safe layer 181.3 ms and first true frame 580.7 ms after the fault. Every take and its numbers: [`evidence/video/README.md`](evidence/video/README.md).

## Bench: 200 injected faults, 0 missed
50 injections per class, one fresh process stack per injection (the guest and Screen persist), always 0.2 s into one of the speed-changing windows of a real 31-minute drive.

| class (50 injections each) | misses | detect p50 / p95 (ms) | stretch target < 100 ms (p95) | formula bound 197 ms (p95) |
|---|---|---|---|---|
| BLANK | 0 | 17.5 / 24.8 | met | met |
| FREEZE | 0 | 106.8 / 129.8 | missed | met |
| STARVE | 0 | 147.6 / 175.8 | missed | met |
| STALE | 0 | 168.2 / 271.2 | n/a: W + 100 = 200 ms target **missed** | n/a (value-detected) |

- Both targets were written before any run in the project's private specs on 2026-11-13, so this public history first contains them after the run (the calibration, `tick_stall_ms` = 151, was committed in `3a911ee` before the first fault row); the bound is `tick_stall_ms` 151 + period 20 + read-back p95 6.3 + 20 ms. Detection time is dominated by the configured `tick_stall_ms` (calibrated on a clean run, [`evidence/calibration/`](evidence/calibration/)), not by monitor speed.
- STALE needs the true speed to move, so it carries the 100 ms truth window by construction; its p95 overshoots the target by about 70 ms, which was not decomposed or tuned afterwards. All 50 STALE rows were caught by the value check; a tick-only watchdog would not have caught them.
- 50 renderer crashes: detect p50 14 ms, 0 missed. 10 MISDRAW (the renderer intends the right value and draws another): 10/10 caught; a self-report check on the renderer's own intent log would have passed all 50 BLANK and 10 MISDRAW runs.
- Safe layer read back on glass p50 22.5-30.1 ms after detection (B1: BLANK, FREEZE, STALE, STARVE); renderer restarted and verified p50 281-338 ms after detection (BLANK, FREEZE, STALE; stamped the way the bench-time monitor did it, see [Conditions and limits](#conditions-and-limits); at the current monitor, 5 per class with the stamp after the window read: p50 307.5-346.4 ms). In the crash bench (`5edab0a`) the safe layer was read back only after the respawn (p50 217.1 ms): that monitor stamped the safe layer on glass only once a renderer window was back (fixed in `645e563`); at the current monitor (`f2b8002`) 5 crashes read it back 18.4-22.6 ms after detection ([`evidence/bench-regress-r05-crash.csv`](evidence/bench-regress-r05-crash.csv)). STARVE is not restarted (the renderer is starved, not hung; the `t_restart_ns` column of its bench rows is empty): the verified true frame returns p50 2.88 s after detection, when the 3 s hog ends.

### Monitor-off ablation
40 runs (10 per class), 30 s window, `ttctl disarm` (the monitor still reads but never acts): the glass was wrong or missing a speed for a **median 29.9 s of 30 s** (minimum 29.09 s; target median >= 25 s, 0 runs below), and still wrong at the end of all 40. A disarmed monitor cannot recover, so this is a control, not a detection result.

### False positives
The first 1800 s of the same drive (1820.8 s measured) with no fault: **0 false positives** in 91,029 armed checks (121 pre-arm warm-up rows excluded and counted in the CSV header). The whole check (three-region read-back plus decode) costs p50 / p95 5.7 / 6.3 ms.

### Period sweep (FREEZE x 50 per period; the 20 ms row is the B1 FREEZE set built at `7592e40`, the 10 and 50 ms rows were built at `f15744a`)
| period_ms | detect p50 / p95 (ms) | safe layer p50 after detection (ms) |
|---|---|---|
| 10 | 100.0 / 122.2 | 25.5 |
| 20 | 106.8 / 129.8 | 26.2 |
| 50 | 106.7 / 156.9 | 49.2 |

## Reproduce
```sh
# needs the QNX SDP 8.0 toolchain and a booted guest, see README "Getting Started"
scripts/bench_all.sh run      # B1 faults, ablation, clean replay, crash, MISDRAW, period sweep -> a fresh evidence/rerun-<UTC>/
                              # (same file names, its own RESULTS.md + report.html); the committed rows are never appended to
scripts/bench_all.sh report   # the committed rows -> evidence/RESULTS.md and evidence/report.html (byte-identical on re-run)
make test-host                # analysis tests, no QNX needed
```
An interrupted re-run resumes with `TT_BENCH_OUT=evidence/rerun-<UTC> scripts/bench_all.sh run` (each row file is its own resume state: run ids already in it are skipped, and the driver says how many). Or re-derive the tables from the committed rows with `scripts/bench.py` (no guest needed).

## Which build ran each bench
Every row file names the commit of the code that ran (`git_sha` column). The detection and recovery code changed after the main benches (code review rounds, cause expiry, the crash-path glass read), so the current monitor was re-checked with a smaller regression run instead of re-running all 200.

| bench | rows | built at |
|---|---|---|
| B1: 200 injected faults | [`evidence/bench-faults.csv`](evidence/bench-faults.csv) | `7592e40` |
| B6: 50 renderer crashes | [`evidence/bench-crash.csv`](evidence/bench-crash.csv) | `5edab0a` |
| B7e: 10 MISDRAW, period sweep 10 / 50 ms | [`evidence/bench-misdraw.csv`](evidence/bench-misdraw.csv), [`evidence/bench-period.csv`](evidence/bench-period.csv) | `f15744a` |
| B2: 40-run monitor-off ablation | [`evidence/bench-ablation.csv`](evidence/bench-ablation.csv) | `d3d5892` |
| B3: 30-minute clean replay | [`evidence/bench-clean.csv`](evidence/bench-clean.csv) | committed in `5edab0a` (the per-check CSV has no `git_sha` column) |
| Regression, 5 per class incl. CRASH, 25/25 detected | [`evidence/bench-regress-r05.csv`](evidence/bench-regress-r05.csv), [`-crash.csv`](evidence/bench-regress-r05-crash.csv) | `f2b8002` (earlier: [`r04`](evidence/bench-regress-r04.csv) at `985ab29`, rows say `7a4367f`) |
| On-target suite, 190 passed / 0 failed | [`evidence/test-target/20261122T172933Z/`](evidence/test-target/20261122T172933Z/summary.txt) | `f2b8002`, re-checked on every push by CI Stage 4 (the run at `985ab29` recorded 163: the checker asserts 27 more since, 190 on both runs) |

## Conditions and limits
- The bench driver runs on the Mac and the stamps are written on the target; host load1 during the runs was 1.8-35.9 and is logged per row. The `runmask` column of the fault-bench files reads `0xf` for every row although STARVE pins the renderer and hog to one CPU.
- Bench runs had no security policy loaded; the policy is exercised by `make test-target` (a FREEZE run with every process in its secpol type, plus 22 secpol checks, 13 of them denials). The renderer was respawned by a shell loop in the bench; `slm` supervision is exercised by the `slm-freeze` test case.
- The `exposure_ms` column reads 0.0 in the main rows (the bench-time monitor did not compute it). Regression checks at the current monitor, 5 per class, 25/25 detected: [`evidence/bench-regress-r05.csv`](evidence/bench-regress-r05.csv) and [`-crash.csv`](evidence/bench-regress-r05-crash.csv), `exposure_ms` 39.4-53.5 ms.
- The hand-back stamp `t_true_ns` (the restore times on this page, the "full" column of `RESULTS.md`, the incident card's "restored") is taken once the read of the renderer's own window that verified the frame has ended (since `f2b8002`). The monitors that wrote the B1, crash, MISDRAW and period rows, the r03 / r04 regression rows, TT-FRZ-0001 and the video takes stamped it at the end of that check's composed capture, before the window read ended: those times are as recorded and read early by about the window read, 3.2 ms in TT-FRZ-0001 and p50 3.6 ms (max 18.9 ms) over the 179 SAFE-HOLD and RESTARTING hand-backs in the per-check logs recorded before the fix (`win_us` in `checks.csv`; discarded video takes excluded). The safe-layer and exposure stamps come from the composed read itself.
- The `smp` column reads 4 in the B1, crash, MISDRAW, period and r03 / r04 regression rows: the bench driver then wrote a constant, `run_qemu.sh`'s default `TT_SMP`, not a value read from the guest. The r05 regression rows take it from the running QEMU's `-smp` (also 4).
- The 200 injections are a declared population, not general coverage. Not run: the B7 holdouts other than MISDRAW, and monitor CPU % per period.

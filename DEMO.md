# Telltale — demo and bench

Every timing on this page is **QEMU (TCG) on an x86_64 QNX 8.0 guest, not hardware timing**. Raw rows are in [`evidence/`](evidence/); generated tables in [`evidence/RESULTS.md`](evidence/RESULTS.md); plots in [`evidence/report.html`](evidence/report.html).

## The demo (one real stop)
The input is a real stop from the comma2k19 dataset: 49.01 km/h to 0 in 4.848 s ([`data/SOURCES.md`](data/SOURCES.md)). A STALE fault makes the cluster keep drawing a speed while the car stops. Without the monitor the glass keeps the old value for the rest of the window; with it, `telltale` reads the digits back, sees the mismatch, posts the true value and restores a verified renderer. One recorded run per fault class: [`evidence/runs/`](evidence/runs/) (`incidents.csv`, `checks.csv`, `telltale.log` per run). The animated hero in the README draws the same drive. The monitor-on run on this stop is [`evidence/runs/p1b-125318-stale`](evidence/runs/p1b-125318-stale/); the landing page and deck replay incident TT-FRZ-0001, a FREEZE injected on the same stop and recorded by `make test-target` at 985ab29 ([`evidence/test-target/20261115T232225Z/t-freeze/`](evidence/test-target/20261115T232225Z/t-freeze/)); the monitor-off figure below is the 40-run ablation on the bench drive, which includes this stop.

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
- Safe layer read back on glass p50 22.5-30.1 ms after detection; renderer restarted and verified p50 281-338 ms after detection (BLANK, FREEZE, STALE). STARVE is not restarted (the renderer is starved, not hung; `t_restart_ns` is empty): the verified true frame returns p50 2.88 s after detection, when the 3 s hog ends.

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
# needs the QNX SDP 8.0 toolchain and a booted guest, see README "Build and run"
scripts/bench_all.sh run      # B1 faults, crash, MISDRAW, ablation, clean replay, period sweep -> evidence/*.csv
scripts/bench_all.sh report   # -> evidence/RESULTS.md and evidence/report.html (byte-identical on re-run)
make test-host                # analysis tests, no QNX needed
```
Or re-derive the tables from the committed rows with `scripts/bench.py` (no guest needed).

## Conditions and limits
- The bench driver runs on the Mac and the stamps are written on the target; host load1 during the runs was 1.8-35.9 and is logged per row. The `runmask` column reads `0xf` for every row although STARVE pins the renderer and hog to one CPU.
- Bench runs had no security policy loaded; the policy is exercised by `make test-target` (a FREEZE run with every process in its secpol type, plus 22 secpol checks, 13 of them denials). The renderer was respawned by a shell loop in the bench; `slm` supervision is exercised by the `slm-freeze` test case.
- The `exposure_ms` column reads 0.0 in the main rows (the bench-time monitor did not compute it). Regression checks at the current monitor, 5 per class, 25/25 detected: [`evidence/bench-regress-r04.csv`](evidence/bench-regress-r04.csv), `exposure_ms` 34.5-54.4 ms.
- The 200 injections are a declared population, not general coverage. Not run: the B7 holdouts other than MISDRAW, and monitor CPU % per period.

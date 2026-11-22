# Discarded B1 attempt 1 (2026-11-15, killed after run 6 of 200)

`attempt1-placement-defect.csv` holds the first rows of the first B1 launch (same code, git_sha in the rows).
Run b1-005 (STALE, window onset 149.5 s) was a MISS, and the cause was the harness, not the monitor:
the injection landed at 0.00 km/h on the last 1.5 km/h step into a standstill, where the held value equals the truth
(a steady-speed STALE is unobservable to a value check; complexity.md B7b). 7 of the 21 census windows are final steps into a
standstill (the 7 stops of the drive) and 3 more (one steady cruise at 74 km/h, two creeping starts at about +1.3 km/h per second) also move
less than 2 km/h within a second. The rule "STALE is injected only where |v(t+1 s) - v(t)| >= 2 km/h at the injection instant" (11 of 21
windows qualify) was added to `scripts/bench_run.py` (`stale_observable`, computed from the trace alone) and B1 was
restarted from run 1 with a clean output file. The discarded rows are kept so the restart is visible, not hidden.

**This supplements the pre-registered placement rule.** complexity.md section 2 fixed "|speed change| >= 1 km/h per 100 ms" (ladder: 1 -> 0.5, never
loosened after a fault run). The window census still uses that rule (K = 21). The new rule is an additional observability filter on STALE only, and it
was added after one fault run showed the defect; it narrows STALE to the 11 windows where the fault can be seen, it does not widen anything.

# Set aside: two runs of regression r05 (2026-11-22, all 25 rows complete each time, `git_sha` f2b8002)

Both runs of the 25-injection regression bench at the monitor with the final-review fixes (`f2b8002`) detected 25 of 25, 0
missed, 0 problem rows, but on a slower guest than every earlier bench: the QEMU process had been started from a zsh `&` job,
and zsh's BG_NICE option ran it at nice 5, so on a busy Mac it got less CPU than usual. Every Screen read slowed down: an
armed NORMAL check took p50 7.0-7.3 ms (`check_us`, [`r05-qemu-nice5/probe/`](r05-qemu-nice5/probe/)) instead of the 3.5 ms
of both on-target suite runs (`985ab29` and `f2b8002`), and the check that raises the safe layer (a full post, `safe_us` up
to 43 ms) ran so long that the next composed read, the first that can show the layer, came 45-58 ms after detection instead
of 21-33 ms: safe layer read back p50 53-59 ms after detection for FREEZE and STARVE in both runs and for STALE in the first
(r04: 26.2-30.7 ms).

- [`r05-hostload/`](r05-hostload/): the first run, also during a Time Machine backup (`host_load1` 11.4-36.1; r04: 5.7-21.0).
  [`r05-hostload/ab/`](r05-hostload/ab/) replays one of its windows (FREEZE, onset 106.9 s, same slice) six times on that
  guest, alternating the current monitor and the `985ab29` build (`ab/ab.sh`; `ck-*.txt` = the guest-side cksum of the binary
  each run used; per-check logs kept): safe layer read back 55.7 / 57.5 / 57.5 ms after detection with the current monitor,
  55.3 / 56.2 / 59.5 ms with `985ab29`. The slowdown is not the fix.
- [`r05-qemu-nice5/`](r05-qemu-nice5/): the second run, after the backup had ended (`host_load1` 5.5-10.7), same niced
  QEMU, FREEZE and STARVE still p50 53-54 ms. Its `probe/` holds three single runs on that guest (the suite's hero FREEZE and
  the bench slice): `check_us` p50 7.0-7.3 ms there too.

QEMU was then restarted at nice 0 (one probe on the bench slice: safe layer 29.3 ms after detection, NORMAL check p50 3.56 ms)
and the regression re-run from run 1 (`evidence/bench-regress-r05*.csv`). These rows are kept so the re-runs are visible, not
hidden.

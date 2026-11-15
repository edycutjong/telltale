# Discarded B1 attempt 1 (2026-11-15, killed after run 6 of 200)

`attempt1-placement-defect.csv` holds the first rows of the first B1 launch (same code, git_sha in the rows).
Run b1-005 (STALE, window onset 149.5 s) was a MISS, and the cause was the harness, not the monitor:
the injection landed at 0.00 km/h on the last 1.5 km/h step into a standstill, where the held value equals the truth
(a steady-speed STALE is unobservable to a value check; complexity.md B7b). 10 of the 21 census windows are such
final steps. The rule "STALE is injected only where |v(t+1 s) - v(t)| >= 2 km/h at the injection instant" (11 of 21
windows qualify) was added to `scripts/bench_run.py` (`stale_observable`, computed from the trace alone) and B1 was
restarted from run 1 with a clean output file. The discarded rows are kept so the restart is visible, not hidden.

**This replaces the pre-registered placement rule.** complexity.md section 2 fixed "|speed change| >= 1 km/h per 100 ms" (ladder: 1 -> 0.5, never
loosened after a fault run). The window census still uses that rule (K = 21). The new rule is an additional observability filter on STALE only, and it
was added after one fault run showed the defect; it narrows STALE to the 11 windows where the fault can be seen, it does not widen anything.

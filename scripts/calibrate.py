#!/usr/bin/env python3
"""Calibration of the monitor's timing thresholds from a CLEAN run (stdlib only).

  calibrate.py <checks.csv> [--period-ms 20] [--readback-p95-us N]

Reads the per-check CSV of a clean replay (monitor not allowed to act: tick_stall_ms was raised for the run) and derives,
by the rule pre-registered in specs (complexity.md section 2, "Threshold pre-registration"):
  tick_stall_ms  >= max(observed renderer inter-frame gap) x 1.5, and never below 2 x period + one frame
Prints the observed gap distribution and the recommended value; the result is committed to scripts/target/telltale.conf
BEFORE any fault run (the git SHA in every bench row proves the order).
A renderer 'frame' is one change of the tick counter the monitor reads from the renderer's heartbeat.
"""
import csv
import gzip
import sys
import argparse


def pct(v, p):
    v = sorted(v)
    return v[max(0, -(-len(v) * p // 100) - 1)] if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("checks")
    ap.add_argument("--period-ms", type=float, default=20.0)
    a = ap.parse_args()
    rows = []
    with (gzip.open(a.checks, 'rt') if a.checks.endswith('.gz') else open(a.checks)) as f:
        for r in csv.DictReader(f):
            try:
                rows.append((int(r["t_cap_start_ns"]), int(r["tick"]), r["state"], r["verdict"], int(r["check_us"])))
            except (ValueError, KeyError, TypeError):
                continue
    live = [r for r in rows if r[2] != "WAIT"]
    gaps, last_t, last_tick = [], None, None
    for t, tick, st, verdict, _ in live:
        if last_tick is None:
            last_t, last_tick = t, tick
            continue
        if tick != last_tick:
            gaps.append((t - last_t) / 1e6)
            last_t, last_tick = t, tick
    chk = [r[4] for r in live]
    span = (live[-1][0] - live[0][0]) / 1e9 if live else 0
    print(f"live checks {len(live)} over {span:.1f} s ({len(rows) - len(live)} warm-up WAIT rows excluded)")
    print(f"renderer frame gaps n={len(gaps)} p50={pct(gaps, 50):.1f} p95={pct(gaps, 95):.1f} p99={pct(gaps, 99):.1f} max={max(gaps):.1f} ms")
    print(f"read-back check_us p50={pct(chk, 50)} p95={pct(chk, 95)} max={max(chk)}")
    rec = max(max(gaps) * 1.5, 2 * a.period_ms + pct(gaps, 50))
    print(f"recommended tick_stall_ms = ceil(max gap x 1.5) = {int(-(-rec // 1))}")
    nonpass = sum(1 for r in live if r[3] != "PASS")
    print(f"non-PASS verdicts among live checks: {nonpass}")


if __name__ == "__main__":
    main()

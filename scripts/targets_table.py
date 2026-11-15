#!/usr/bin/env python3
"""Measured p95 detection vs the targets fixed before the runs (stdlib only; reads evidence/*.csv).

  targets_table.py

Targets (specs, pre-registered 2026-11-13, neither restated): (a) stretch: FREEZE, BLANK, STARVE p95 < 100 ms and STALE p95 < W + 100 ms;
(b) formula bound for the tick-detected classes: p95 <= tick_stall_ms + period + read-back p95 + 20 ms. STALE is value-detected, so only (a) applies to it.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402


def main() -> int:
    ev = ROOT / "evidence"
    rows = [r for r in csv.DictReader(l for l in (ev / "bench-faults.csv").read_text().splitlines() if not l.startswith("#"))]
    clean = bench.load([str(ev / "bench-clean.csv")], bench.CHECK_COLUMNS)
    rb = bench.analyze_clean(clean.rows, "tcg")["readback_check_us"]["p95"] / 1000.0
    tick, period, w = float(rows[0]["tick_stall_ms"]), float(rows[0]["period_ms"]), 100.0
    bound = tick + period + rb + 20.0
    per: dict[str, list[float]] = {}
    for r in rows:
        if r["missed"] == "0":
            per.setdefault(r["class"], []).append((int(r["t_detect_ns"]) - int(r["t_inject_ns"])) / 1e6)
    print(f"Formula bound (b) = tick_stall_ms {tick:.0f} + period {period:.0f} + read-back p95 {rb:.2f} + 20 = {bound:.1f} ms; STALE target (a) = W + 100 = {w + 100:.0f} ms.\n")
    print("| class | n | misses | p95 detect (ms) | stretch < 100 ms | formula bound " + f"{bound:.0f} ms |")
    print("|---|---|---|---|---|---|")
    for cls in ("BLANK", "FREEZE", "STARVE", "STALE"):
        v = per.get(cls)
        if not v:
            continue
        p95 = bench.percentile(v, 95)
        miss = sum(1 for r in rows if r["class"] == cls and r["missed"] == "1")
        if cls == "STALE":
            print(f"| {cls} | {len(v)} | {miss} | {p95:.1f} | n/a (W + 100 = 200: {'met' if p95 < 200 else 'MISSED'}) | n/a (value-detected) |")
        else:
            print(f"| {cls} | {len(v)} | {miss} | {p95:.1f} | {'met' if p95 < 100 else 'MISSED'} | {'met' if p95 <= bound else 'MISSED'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())

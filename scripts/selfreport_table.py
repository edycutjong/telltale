#!/usr/bin/env python3
"""What a renderer self-report cannot see: BLANK and MISDRAW rows, from the renderer's own log (stdlib only).

  selfreport_table.py                      # reads evidence/bench-runs/{b1,b7e}/*/ (cluster-bench.csv + incidents.csv)

For the two classes where the renderer draws something other than what it believes, the renderer's own bench log
(cluster-bench.csv, written on the target at the fault) records the value it INTENDED to show. A "self-report" monitor
(intended value vs truth, plus the tick heartbeat) therefore sees intended == truth and passes; the pixel read does not.
Only these two classes are scored here: for FREEZE / STALE / STARVE the intended-value log was not collected, so no
self-report claim is made for them (complexity.md expects a self-report to catch those too).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cases(root: Path) -> list[dict]:
    out = []
    for d in sorted(root.glob("*")):
        cb, inc = d / "cluster-bench.csv", d / "incidents.csv"
        if not (cb.exists() and inc.exists()):
            continue
        fault = next((ln.split(",") for ln in cb.read_text().splitlines() if len(ln.split(",")) >= 6 and ln.split(",")[2] in ("BLANK", "MISDRAW")), None)
        rows = [ln.split(",") for ln in inc.read_text().splitlines() if ln and not ln.startswith("id,")]
        if not fault or not rows:
            continue
        r = rows[0]
        if int(r[6]) < int(fault[3]):
            continue
        shown, truth, intended = int(r[3]), int(r[4]), int(fault[4])
        out.append({"run": d.name, "cls": fault[2], "intended": intended, "truth": truth, "decoded": shown,
                    "detect_ms": (int(r[6]) - int(fault[3])) / 1e6, "self_ok": abs(intended - truth) <= 1})
    return out


def main() -> int:
    ev = ROOT / os.environ.get("TT_EVIDENCE", "evidence")   # bench_all.sh report sets it (default: the committed rows)
    rows = cases(ev / "bench-runs/b1") + cases(ev / "bench-runs/b7e")
    if not rows:
        print("no BLANK / MISDRAW runs found", file=sys.stderr)
        return 1
    print("| class | runs | pixel read detected | self-report (intended vs truth) would have passed | example: intended / truth / decoded |")
    print("|---|---|---|---|---|")
    for cls in ("BLANK", "MISDRAW"):
        rs = [r for r in rows if r["cls"] == cls]
        if rs:
            ex = rs[0]
            det = sum(r["detect_ms"] <= 2000 for r in rs)
            print(f"| {cls} | {len(rs)} | {det}/{len(rs)} (within 2 s) | {sum(r['self_ok'] for r in rs)}/{len(rs)} | {ex['intended']} / {ex['truth']} / {ex['decoded']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())

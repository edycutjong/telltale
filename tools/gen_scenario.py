#!/usr/bin/env python3
"""Write the scripted lamp/gear scenario for the hero drive (data/scenario-hero.csv), seed-data.md rules.

Rows are change points in trace time: t_s,lamps,gear (lamps = veh_state bits: 0 turn L, 1 turn R, 2 brake,
3 ABS, 4 seat belt, 5 high beam). Deterministic: derived only from trace-hero.meta.
  - gear D throughout
  - seat-belt lamp lit for the first 2 s
  - left turn blinking at 90 flashes/min (on half of each 0.667 s cycle) from brake onset to the stop
  - brake-system lamp lit from 1 s before the stop to the end (the red lamp the safe layer must carry)

  python3 tools/gen_scenario.py [--meta data/trace-hero.meta] [--out data/scenario-hero.csv] [--check]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TURN_L, BRAKE, BELT = 1 << 0, 1 << 2, 1 << 4
BLINK_PERIOD_S = 60.0 / 90.0


def read_meta(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def scenario_rows(onset_s: float, stop_s: float) -> list[tuple[float, int, str]]:
    """Change points (t_s, lamps, gear), sorted, consecutive duplicates removed."""
    turn: list[tuple[float, bool]] = []          # blink edges, exact half-cycles counted by index
    k = 0
    while onset_s + k * BLINK_PERIOD_S / 2 < stop_s:
        turn.append((round(onset_s + k * BLINK_PERIOD_S / 2, 3), k % 2 == 0))
        k += 1
    turn.append((round(stop_s, 3), False))
    brake_s = round(stop_s - 1.0, 3)
    times = sorted({0.0, 2.0, brake_s, *(t for t, _ in turn)})

    def lamps_at(ts: float) -> int:
        bits = BELT if ts < 2.0 else 0
        on = False
        for t, state in turn:
            if t <= ts:
                on = state
        if on:
            bits |= TURN_L
        if ts >= brake_s:
            bits |= BRAKE
        return bits

    rows: list[tuple[float, int, str]] = []
    for ts in times:
        b = lamps_at(ts)
        if not rows or rows[-1][1] != b:
            rows.append((ts, b, "D"))
    return rows


def render(rows: list[tuple[float, int, str]]) -> str:
    return "t_s,lamps,gear\n" + "".join(f"{t:.3f},0x{b:02x},{g}\n" for t, b, g in rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--meta", default="data/trace-hero.meta")
    ap.add_argument("--out", default="data/scenario-hero.csv")
    ap.add_argument("--check", action="store_true", help="exit 1 if --out differs from a fresh render")
    a = ap.parse_args(argv)
    m = read_meta(Path(a.meta))
    text = render(scenario_rows(float(m["brake_onset_s"]), float(m["stop_s"])))
    out = Path(a.out)
    if a.check:
        ok = out.exists() and out.read_text() == text
        print("scenario: up to date" if ok else "scenario: STALE")
        return 0 if ok else 1
    out.write_text(text)
    print(f"scenario: {out} ({text.count(chr(10)) - 1} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

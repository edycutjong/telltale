#!/usr/bin/env python3
"""Assert what one recorded Telltale run on the QNX target must show (used by scripts/test_target.sh).

  python3 scripts/check_run.py <run-dir> <class>
  class: freeze blank stale misdraw crash lampdrop starve accept none

Reads <run-dir>/incidents.csv (the monitor's incident rows) and inject.txt / hog.log (the fault command time).
Prints one PASS/FAIL line per assertion; exit 0 iff all pass.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

COLS = ["id", "verdict", "cause", "shown", "truth", "t_last_verified_ns", "t_detect_ns", "caught_ms",
        "t_safe_visible_ns", "t_kill_ns", "t_register_ns", "t_true_ns", "restarts", "exposure_ms",
        "renderer_pid", "new_pid", "lamps_shown", "lamps_truth"]

# class -> (verdicts allowed, causes allowed or None, kill expected: True/False/None)
EXPECT = {
    "freeze": ({"TICK_STALL", "VALUE_MISMATCH"}, {"hung"}, True),   # mismatch first if the speed moves
    "blank": ({"BLANK", "UNREADABLE"}, None, True),
    "stale": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "misdraw": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "crash": ({"TICK_STALL", "BLANK", "UNREADABLE", "NO_DISPLAY", "VALUE_MISMATCH"}, {"crash"}, None),
    "lampdrop": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "starve": ({"TICK_STALL", "VALUE_MISMATCH"}, {"starved"}, False),   # mismatch first if the speed moves
    "accept": ({"VALUE_MISMATCH"}, None, False),
}


WARNINGS: list[str] = []


def read_incidents(run: Path) -> list[dict[str, str]]:
    path = run / "incidents.csv"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("id,"):
            continue
        rows.append(dict(zip(COLS, line.split(","))))
    return rows


def fault_time_ns(run: Path) -> int | None:
    for name, key in (("inject.txt", "t_command_ns"), ("hog.log", "t_hog_start_ns")):
        p = run / name
        if p.exists():
            m = re.search(key + r"=(\d+)", p.read_text())
            if m:
                return int(m.group(1))
    return None


def check(run: Path, cls: str) -> list[tuple[bool, str]]:
    inc = read_incidents(run)
    out: list[tuple[bool, str]] = []
    if cls == "none":
        out.append((len(inc) == 0, f"clean drive: {len(inc)} incidents (want 0)"))
        return out
    verdicts, causes, kill = EXPECT[cls]
    if not inc:
        out.append((False, "0 incidents (want 1)"))
        return out
    r = inc[0]
    # extra incidents: a TICK_STALL that resolved by itself (verified-true frame again, no kill) is a spurious stall on
    # QEMU (TCG) -- reported as WARN and counted by the false-positive bench (B3), not a test failure here; anything
    # else (a kill, another verdict, never resolved) is flapping and fails
    t_true0 = int(r["t_true_ns"])
    extra = inc[1:]
    settle = [x for x in extra if x["verdict"] == "TICK_STALL" and int(x["t_kill_ns"]) == 0 and int(x["t_true_ns"]) > 0]
    out.append((len(extra) == len(settle), f"{len(inc)} incident(s): 1 fault + {len(settle)} self-resolved spurious stall(s)"
                f" + {len(extra) - len(settle)} other (want 0 other: no flapping)"))
    for x in settle:
        WARNINGS.append(f"spurious stall (QEMU TCG, counted in B3) {x['id']} {float(x['caught_ms']):.0f} ms after the last verified frame, "
                        f"{(int(x['t_detect_ns']) - t_true0) / 1e6:.0f} ms after hand-back, no kill")
    out.append((r["verdict"] in verdicts, f"verdict {r['verdict']} in {sorted(verdicts)}"))
    if causes:
        out.append((r["cause"] in causes, f"cause {r['cause']} in {sorted(causes)}"))
    t_fault = fault_time_ns(run)
    t_detect = int(r["t_detect_ns"])
    out.append((t_fault is not None and t_detect > t_fault, f"detected after the fault command (t_detect {t_detect} > {t_fault})"))
    out.append((int(r["t_safe_visible_ns"]) > t_detect, "safe layer read back visible after detection"))
    killed = int(r["t_kill_ns"]) > 0
    if kill is True:
        out.append((killed, "renderer killed (t_kill set)"))
    elif kill is False:
        out.append((not killed and int(r["restarts"]) == 0, "no kill, no restart"))
    out.append((int(r["t_true_ns"]) > t_detect, "hand-back: a verified-true renderer frame after detection (t_true set)"))
    if cls == "accept":
        p = run / "heal.txt"
        m = re.search(r"t_command_ns=(\d+)", p.read_text()) if p.exists() else None
        t_heal = int(m.group(1)) if m else None
        out.append((t_heal is not None and t_detect < t_heal < int(r["t_true_ns"]),
                    f"heal sent during the hold, before the hand-back (t_heal {t_heal})"))
    if cls == "misdraw":
        out.append((r["shown"] != r["truth"], f"shown {r['shown']} != truth {r['truth']}"))
    if cls == "lampdrop":
        out.append((r.get("lamps_shown") != r.get("lamps_truth"), f"lamps shown {r.get('lamps_shown')} != truth {r.get('lamps_truth')}"))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in [*EXPECT, "none"]:
        print(__doc__)
        return 2
    run, cls = Path(argv[1]), argv[2]
    res = check(run, cls)
    for ok, msg in res:
        print(f"{'PASS' if ok else 'FAIL'} {cls}: {msg}")
    for w in WARNINGS:
        print(f"WARN {cls}: {w}")
    return 0 if all(ok for ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

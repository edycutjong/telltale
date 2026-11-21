#!/usr/bin/env python3
"""Per-take numbers for the demo video, re-derived from a run's raw files with the bench's own definitions.

    python3 tools/video_numbers.py evidence/video/<run> [...]      -> prints and writes <run>/numbers.txt

t0 is the fault's effective stamp from the run's cluster-bench.csv: the same stamp `scripts/bench_run.make_row`
uses as t_inject_ns for the B1 bench. Each incident event in incidents.csv (last verified frame, detect, safe layer
visible, renderer killed, renderer re-registered, first true frame) is reported as ms after t0, rounded to 0.1 ms.
These are the "this run" values on screen in the video. Bench p50/p95 come from evidence/RESULTS.md, not from here.
All stamps are CLOCK_MONOTONIC on the QNX 8.0 guest under QEMU (TCG): emulator timing, not hardware timing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bench_run import read_incidents  # noqa: E402

CLS = {"freeze": "FREEZE", "stale": "STALE", "misdraw": "MISDRAW", "crash": "CRASH", "blank": "BLANK"}
EVENTS = [("last true frame", "t_last_verified_ns"), ("caught", "t_detect_ns"), ("safe layer", "t_safe_visible_ns"),
          ("renderer killed", "t_kill_ns"), ("renderer back", "t_register_ns"), ("true frame", "t_true_ns")]


def fault_stamp(run: Path, cls: str) -> int | None:
    bench = run / "cluster-bench.csv"
    if not bench.exists():
        return None
    for line in bench.read_text().splitlines():
        f = line.split(",")
        if len(f) >= 4 and f[2] == cls:
            return int(f[3])
    return None


def ms_after(v: str, t0: int | None) -> str:
    if t0 is None or v in ("", "0", "-1"):
        return "-"
    return f"{(int(v) - t0) / 1e6:+.1f} ms"


def numbers(run: Path) -> list[str]:
    cls = CLS[run.name.rsplit("-", 1)[1]]
    t0 = fault_stamp(run, cls)
    incs = read_incidents(run)
    out = [f"run {run.name}  class {cls}  fault effective stamp {t0 if t0 is not None else '-'} ns (cluster-bench.csv)",
           f"incidents {len(incs)}"]
    for i in incs:
        ev = "  ".join(f"{name} {ms_after(i[col], t0)}" for name, col in EVENTS)
        out.append(f"{i['id']} {i['verdict']} cause={i['cause']} shown={i['shown']} truth={i['truth']} "
                   f"card_caught_ms={i['caught_ms']} exposure_ms={i['exposure_ms']} restarts={i['restarts']}")
        out.append(f"  after the fault: {ev}")
    return out


def main(argv: list[str]) -> int:
    for a in argv:
        run = Path(a)
        lines = numbers(run)
        (run / "numbers.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

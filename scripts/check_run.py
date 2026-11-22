#!/usr/bin/env python3
"""Assert what one recorded Telltale run on the QNX target must show (used by scripts/test_target.sh).

  python3 scripts/check_run.py <run-dir> <class>
  class: freeze blank stale misdraw crash lampdrop starve accept debug none
  debug = the accept sequence in engineering mode: the banner text and the incident card must be logged with this
  incident's values AND be present in the frames ttshot read back from the composed display (banner.png, card.png).

Reads <run-dir>/incidents.csv (the monitor's incident rows) and inject.txt / hog.log (the fault command time).
Prints one PASS/FAIL line per assertion; exit 0 iff all pass.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# incidents.csv has no header row; this list IS the schema and equals telltale.c EV_HEADER (tests/host/test_incident_schema.py).
# Older recorded runs wrote fewer columns: 16 (before the lamp fields) and 18 (before t_safe_true_ns, a2a round 04).
# A row of any other width is schema drift and raises; check() asserts the truth-on-glass stamp only where it exists.
COLS = ["id", "verdict", "cause", "shown", "truth", "t_last_verified_ns", "t_detect_ns", "caught_ms",
        "t_safe_visible_ns", "t_kill_ns", "t_register_ns", "t_true_ns", "restarts", "exposure_ms",
        "renderer_pid", "new_pid", "lamps_shown", "lamps_truth", "t_safe_true_ns"]
SCHEMA_WIDTHS = (16, 18, len(COLS))

# class -> (verdicts allowed, causes allowed or None, kill expected: True/False/None)
EXPECT = {
    "freeze": ({"TICK_STALL", "VALUE_MISMATCH"}, {"hung"}, True),   # mismatch first if the speed moves
    # blank: the first composed read can straddle the frame change (digit block read before the cleared frame, lamp row
    # after it) and see "lamps missing" = VALUE_MISMATCH; the renderer's own window then reads BLANK, which is the cause
    "blank": ({"BLANK", "UNREADABLE", "VALUE_MISMATCH"}, {"BLANK", "UNREADABLE"}, True),
    "stale": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "misdraw": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "crash": ({"TICK_STALL", "BLANK", "UNREADABLE", "VALUE_MISMATCH"}, {"crash"}, None),
    "lampdrop": ({"VALUE_MISMATCH"}, {"live-wrong"}, True),
    "starve": ({"TICK_STALL", "VALUE_MISMATCH"}, {"starved"}, False),   # mismatch first if the speed moves
    "accept": ({"VALUE_MISMATCH"}, None, False),
    "debug": ({"VALUE_MISMATCH"}, None, False),
}

# display geometry (src/common/layout.h): window at 0,24; banner rows 600..663; card 868,120 404x188 (window offsets)
WIN_Y = 24
BANNER = (0, WIN_Y + 600, 1280, 64)
CARD = (868, WIN_Y + 120, 404, 188)
AMBER, CARD_BG, LIT = (0xF2, 0xA9, 0x00), (0x1A, 0x19, 0x16), (0xF4, 0xF1, 0xE8)


def near(p, c, tol=24):
    return all(abs(a - b) <= tol for a, b in zip(p, c))


def check_debug(run: Path, r: dict[str, str]) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    log = (run / "telltale.log").read_text(errors="replace") if (run / "telltale.log").exists() else ""
    tlv, tdet, ttrue = int(r["t_last_verified_ns"]), int(r["t_detect_ns"]), int(r["t_true_ns"])
    caught, restored = f"{(tdet - tlv) / 1e6:.0f}", f"{(ttrue - tlv) / 1e6:.0f}"
    want_banner = f"ENGINEERING VIEW \u00b7 DISPLAY FAULT CAUGHT \u00b7 {r['verdict']} \u00b7 {caught} ms"
    m = re.search(r'BANNER x=(\d+) w=(\d+) mode=engineering text="([^"]*)"', log)
    out.append((m is not None and m.group(3) == want_banner, f"engineering banner logged with this incident's class + caught ms ({want_banner!r})"))
    mc = re.search(r'CARD shown (\S+) at \d+,\d+ \d+x\d+ rows="([^"]*)"', log)
    rows = mc.group(2).split("|") if mc else []
    row1 = r["verdict"] if r["cause"] == "-" else f"{r['verdict']} \u00b7 {r['cause']}"
    if r["shown"] == r["truth"] and r.get("lamps_shown") != r.get("lamps_truth"):
        row2 = f"lamps shown {int(r['lamps_shown'], 16):02x} \u00b7 truth {int(r['lamps_truth'], 16):02x}"
    elif int(r["shown"]) >= 0:
        row2 = f"shown {r['shown']} \u00b7 truth {r['truth']} km/h"
    else:
        row2 = f"shown -- \u00b7 truth {r['truth']} km/h"
    want_rows = {0: f"INCIDENT {r['id']}", 1: row1, 2: row2, 3: f"caught {caught} ms \u00b7 restored {restored} ms",
                 4: "handed back, no restart", 5: "service: cat /dev/telltale"}
    out.append((len(rows) == 6 and all(rows[i] == v for i, v in want_rows.items()),
                f"incident card logged, every row = this incident's incidents.csv values ({rows})"))
    out.append((f"CARD hidden {r['id']} (timeout)" in log, "card hidden after card_ms"))
    try:
        from PIL import Image
    except ImportError:
        out.append((False, "Pillow needed to read back banner.png / card.png"))
        return out
    bp, cp = run / "banner.png", run / "card.png"
    if bp.exists() and m:
        img = Image.open(bp).convert("RGB")
        x0, y0, w, h = BANNER
        px = img.crop((x0, y0, x0 + w, y0 + h)).load()
        cols = [x for x in range(w) if any(not near(px[x, y], AMBER) for y in range(h))]
        amber = sum(near(px[x, y], AMBER) for x in range(w) for y in range(h)) / (w * h)
        lx, lw = int(m.group(1)), int(m.group(2))
        ok = bool(cols) and abs(cols[0] - lx) <= 4 and abs(cols[-1] + 1 - (lx + lw)) <= 6 and 0.6 <= amber < 0.98
        out.append((ok, f"banner read back: amber {amber:.2f}, text spans x {cols[0] if cols else '-'}..{cols[-1] + 1 if cols else '-'}"
                        f" vs drawn {lx}..{lx + lw} (+-4/6 px)"))
    else:
        out.append((False, "banner.png captured (ttshot)"))
    if cp.exists():
        img = Image.open(cp).convert("RGB")
        x0, y0, w, h = CARD
        px = img.crop((x0, y0, x0 + w, y0 + h)).load()
        bg = sum(near(px[x, y], CARD_BG, 6) for x in range(w) for y in range(h)) / (w * h)
        lit = sum(near(px[x, y], LIT, 6) for x in range(w) for y in range(h))
        stripe = all(near(px[x, y], AMBER) for x in range(w) for y in range(4))
        out.append((bg >= 0.6 and lit >= 200 and stripe, f"card read back: background {bg:.2f}, {lit} lit text px, amber top stripe {stripe}"))
    else:
        out.append((False, "card.png captured (ttshot)"))
    hp = run / "card-hidden.png"
    if hp.exists():
        img = Image.open(hp).convert("RGB")
        x0, y0, w, h = CARD
        px = img.crop((x0, y0, x0 + w, y0 + h)).load()
        bg = sum(near(px[x, y], CARD_BG, 6) for x in range(w) for y in range(h)) / (w * h)
        stripe = sum(near(px[x, y], AMBER) for x in range(w) for y in range(4)) / (w * 4)
        out.append((bg < 0.2 and stripe < 0.2, f"card gone from the frame after card_ms: background {bg:.2f}, stripe {stripe:.2f} (want < 0.2)"))
    else:
        out.append((False, "card-hidden.png captured (ttshot)"))
    return out


WARNINGS: list[str] = []
MISS_NS = 2_000_000_000     # spec: missed = no detection within 2 s
SETTLE_NS = 2_000_000_000   # a post-hand-back settle stall must start within 2 s of the hand-back


def read_incidents(run: Path) -> list[dict[str, str]]:
    path = run / "incidents.csv"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("id,"):
            continue
        f = line.split(",")
        if len(f) not in SCHEMA_WIDTHS:
            raise ValueError(f"{path}: {len(f)} fields, want one of {SCHEMA_WIDTHS} (incident CSV schema drift)")
        rows.append(dict(zip(COLS, f)))
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
    out: list[tuple[bool, str]] = []
    if not (run / "incidents.csv").is_file():   # a run that left no incident log proves nothing, not even "clean"
        return [(False, f"{run / 'incidents.csv'} exists (the monitor's incident log was collected)")]
    inc = read_incidents(run)
    if cls == "none":
        out.append((len(inc) == 0, f"clean drive: {len(inc)} incidents (want 0)"))
        return out
    verdicts, causes, kill = EXPECT[cls]
    if not inc:
        out.append((False, "0 incidents (want 1)"))
        return out
    r = inc[0]
    # extra incidents: at most one TICK_STALL within 2 s of the hand-back that resolved by itself (no kill) is the
    # post-recovery Screen settle on QEMU (TCG) -- WARN, counted by the bench (B3); anything else is flapping and fails
    t_true0 = int(r["t_true_ns"])
    extra = inc[1:]
    settle = [x for x in extra if x["verdict"] == "TICK_STALL" and int(x["t_kill_ns"]) == 0 and int(x["t_true_ns"]) > 0
              and t_true0 > 0 and 0 <= int(x["t_detect_ns"]) - t_true0 < SETTLE_NS]
    out.append((len(extra) == len(settle) and len(settle) <= 1,
                f"{len(inc)} incident(s): 1 fault + {len(settle)} self-resolved stall(s) within 2 s of hand-back"
                f" + {len(extra) - len(settle)} other (want <= 1 settle stall, 0 other: no flapping)"))
    for x in settle:
        WARNINGS.append(f"spurious stall (QEMU TCG, counted in B3) {x['id']} {float(x['caught_ms']):.0f} ms after the last verified frame, "
                        f"{(int(x['t_detect_ns']) - t_true0) / 1e6:.0f} ms after hand-back, no kill")
    out.append((r["verdict"] in verdicts, f"verdict {r['verdict']} in {sorted(verdicts)}"))
    if causes:
        out.append((r["cause"] in causes, f"cause {r['cause']} in {sorted(causes)}"))
    t_fault = fault_time_ns(run)
    t_detect = int(r["t_detect_ns"])
    out.append((t_fault is not None and t_detect > t_fault, f"detected after the fault command (t_detect {t_detect} > {t_fault})"))
    if t_fault is not None:   # the spec's "missed" bound: no detection within 2 s of the fault
        out.append((t_detect - t_fault < MISS_NS, f"detected {(t_detect - t_fault) / 1e6:.0f} ms after the fault command (< 2000 ms)"))
    out.append((int(r["t_safe_visible_ns"]) > t_detect, "safe layer read back visible after detection"))
    if "t_safe_true_ns" in r:   # current schema: the correction itself verified on glass, and exposure ends there
        t_st, t_last = int(r["t_safe_true_ns"]), int(r["t_last_verified_ns"])
        out.append((t_st > t_detect, "truth verified on glass: a composed read of the safe layer equal to the truth after detection (t_safe_true set)"))
        end = t_st if t_st else int(r["t_true_ns"])
        want = (end - t_last) / 1e6 if end > t_last else -1.0
        out.append((abs(float(r["exposure_ms"]) - want) <= 0.051,
                    f"exposure_ms {r['exposure_ms']} = last verified frame -> truth on glass ({want:.2f} ms; rows print 0.1 ms)"))
    killed = int(r["t_kill_ns"]) > 0
    if kill is True:
        out.append((killed, "renderer killed (t_kill set)"))
    elif kill is False:
        out.append((not killed and int(r["restarts"]) == 0, "no kill, no restart"))
    out.append((int(r["t_true_ns"]) > t_detect, "hand-back: a verified-true renderer frame after detection (t_true set)"))
    if cls in ("accept", "debug"):
        p = run / "heal.txt"
        m = re.search(r"t_command_ns=(\d+)", p.read_text()) if p.exists() else None
        t_heal = int(m.group(1)) if m else None
        out.append((t_heal is not None and t_detect < t_heal < int(r["t_true_ns"]),
                    f"heal sent during the hold, before the hand-back (t_heal {t_heal})"))
    if cls == "debug":
        out += check_debug(run, r)
    if cls == "crash":
        out.append((int(r["new_pid"]) > 0 and r["new_pid"] != r["renderer_pid"], f"a new renderer registered (pid {r['renderer_pid']} -> {r['new_pid']})"))
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

#!/usr/bin/env python3
"""Assert what one recorded Telltale run on the QNX target must show (used by scripts/test_target.sh).

  python3 scripts/check_run.py <run-dir> <class> [--width N]
  --width N: every incident row must have exactly N columns (the live suite and the CI replay pass 19, the current schema,
  so a current row cut short cannot pass as an older schema and skip the truth-on-glass checks)
  class: freeze blank stale misdraw crash lampdrop starve accept debug none
  debug = the accept sequence in engineering mode: the banner text and the incident card must be logged with this
  incident's values AND be present in the frames ttshot read back from the composed display (banner.png, card.png): the
  banner and each card row are rendered here from this incident's incidents.csv values with the monitor's own glyph atlas
  (src/common/textfont.h, the blend of src/common/text.c) and must equal the captured pixels.
  none = a clean drive: no incident record, the monitor's status at collection NORMAL with faults=0 (no incident open
  either: a record is only written when an incident closes), >= 1000 armed NORMAL checks passed, and no check after
  arming that is anything other than an armed NORMAL PASS.

Reads <run-dir>/incidents.csv (the monitor's incident rows) and inject.txt / hog.log (the fault command time).
Prints one PASS/FAIL line per assertion; exit 0 iff all pass.
"""
from __future__ import annotations

import csv
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
CARD_PAD, CARD_PITCH = 12, 28                                   # layout.h L_CARD_PAD, L_CARD_PITCH
AMBER, CARD_BG, LIT = (0xF2, 0xA9, 0x00), (0x1A, 0x19, 0x16), (0xF4, 0xF1, 0xE8)
GLASS, MUTED = (0x0E, 0x0D, 0x0B), (0x8A, 0x85, 0x7A)           # palette.h PAL_GLASS, PAL_MUTED
TEXTFONT = Path(__file__).resolve().parent.parent / "src" / "common" / "textfont.h"


def near(p, c, tol=24):
    return all(abs(a - b) <= tol for a, b in zip(p, c))


def load_font(name: str) -> dict:
    """tt_font_<name> from src/common/textfont.h: line height, advance / ink offset / ink width per glyph, 4-bit coverage."""
    src = TEXTFONT.read_text()

    def arr(field: str) -> list[int]:
        m = re.search(r"static const (?:unsigned char|signed char|unsigned) tf_" + name + "_" + field + r"\[TT_FONT_N\] = \{([^}]*)\};", src)
        return [int(x) for x in m.group(1).split(",")]
    data = re.search(r"static const char tf_" + name + r"_data\[\] =(.*?);", src, re.S).group(1)
    h = int(re.search(r"const struct tt_font tt_font_" + name + r" = \{(\d+),", src).group(1))
    return {"h": h, "adv": arr("adv"), "xo": arr("xo"), "w": arr("w"), "off": arr("off"), "data": "".join(re.findall(r'"([0-9a-f]*)"', data))}


def glyphs(s: str):
    """src/common/text.c next_glyph(): ASCII 32..126, U+00B7 = index 95, any other byte draws nothing."""
    b, i = s.encode("utf-8"), 0
    while i < len(b):
        c, i = b[i], i + 1
        if 32 <= c <= 126:
            yield c - 32
        elif c == 0xC2 and i < len(b) and b[i] == 0xB7:
            i += 1
            yield 95


def text_width(f: dict, s: str) -> int:
    return sum(f["adv"][g] for g in glyphs(s))


def draw_text(canvas: list[list[tuple]], x: int, y: int, f: dict, s: str, fg: tuple, bg: tuple) -> None:
    """src/common/text.c tt_text_draw(): coverage a (0..15) blends fg over bg per channel, (bg*(15-a) + fg*a + 7) / 15."""
    h, w = len(canvas), len(canvas[0])
    for g in glyphs(s):
        cov, gx = f["data"][f["off"][g]:], x + f["xo"][g]
        for r in range(f["h"]):
            if not 0 <= y + r < h:
                continue
            for c in range(f["w"][g]):
                a = int(cov[r * f["w"][g] + c], 16)
                if a and 0 <= gx + c < w:
                    canvas[y + r][gx + c] = tuple((b_ * (15 - a) + f_ * a + 7) // 15 for f_, b_ in zip(fg, bg))
        x += f["adv"][g]


def frame_diff(img, box: tuple[int, int, int, int], want: list[list[tuple]], tol: int = 8) -> int:
    """Pixels of the captured frame inside box that differ from the expected rendering by more than tol on any channel."""
    x0, y0, w, h = box
    px = img.crop((x0, y0, x0 + w, y0 + h)).load()
    return sum(1 for y in range(h) for x in range(w) if any(abs(a - b) > tol for a, b in zip(px[x, y], want[y][x])))


def expected_banner(text: str) -> list[list[tuple]]:
    """telltale.c banner_update(): amber band, text centred, glass-coloured, vertically centred in the band."""
    f, (_, _, w, h) = load_font("banner"), BANNER
    canvas = [[AMBER] * w for _ in range(h)]
    draw_text(canvas, (w - text_width(f, text)) // 2, (h - f["h"]) // 2, f, text, GLASS, AMBER)
    return canvas


def expected_card(rows: list[str]) -> list[list[tuple]]:
    """telltale.c card_show(): card background, 4 px amber stripe, six rows (amber, four lit, muted) at pad + i * pitch."""
    f, (_, _, w, h) = load_font("card"), CARD
    canvas = [[CARD_BG] * w for _ in range(h)]
    canvas[:4] = [[AMBER] * w for _ in range(4)]
    for i, (row, fg) in enumerate(zip(rows, (AMBER, LIT, LIT, LIT, LIT, MUTED))):
        draw_text(canvas, CARD_PAD, CARD_PAD + i * CARD_PITCH, f, row, fg, CARD_BG)
    return canvas


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
        bad = frame_diff(img, BANNER, expected_banner(want_banner))
        out.append((bad == 0, f"banner text in the frame = this incident's banner rendered with the monitor's atlas: "
                              f"{bad} of {BANNER[2] * BANNER[3]} px differ (want 0)"))
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
        bad = frame_diff(img, CARD, expected_card([want_rows[i] for i in range(6)]))
        out.append((bad == 0, f"card text in the frame = this incident's 6 rows rendered with the monitor's atlas: "
                              f"{bad} of {CARD[2] * CARD[3]} px differ (want 0)"))
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


def read_incidents(run: Path, width: int | None = None) -> list[dict[str, str]]:
    path = run / "incidents.csv"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("id,"):
            continue
        f = line.split(",")
        if width is not None and len(f) != width:
            raise ValueError(f"{path}: {len(f)} fields, the declared schema has {width}")
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


def check(run: Path, cls: str, width: int | None = None) -> list[tuple[bool, str]]:
    out: list[tuple[bool, str]] = []
    if not (run / "incidents.csv").is_file():   # a run that left no incident log proves nothing, not even "clean"
        return [(False, f"{run / 'incidents.csv'} exists (the monitor's incident log was collected)")]
    inc = read_incidents(run, width)
    if cls == "none":
        out.append((len(inc) == 0, f"clean drive: {len(inc)} incidents (want 0)"))
        # an incident record is written only when the incident closes: one still open at collection leaves incidents.csv
        # empty, so the monitor's own status (ttctl status, taken at collection) must say NORMAL with faults=0
        sp = run / "status.txt"
        st = sp.read_text(errors="replace") if sp.is_file() else ""
        ms, mf = re.search(r"\bstate=(\S+)", st), re.search(r"\bfaults=(\d+)", st)
        out.append((bool(ms and mf) and ms.group(1) == "NORMAL" and mf.group(1) == "0",
                    f"monitor status at collection: state={ms.group(1) if ms else '?'} faults={mf.group(1) if mf else '?'} "
                    f"(want NORMAL, 0: no incident open or closed)"))
        # zero incidents only counts if the drive was checked: the monitor's per-check log must hold armed NORMAL checks
        # that passed (>= 1000 = 20 s at period_ms 20; the 56 s hero drive gives ~3,000)
        cp = run / "checks.csv"
        rows = []
        if cp.is_file():
            with cp.open(newline="") as fh:
                rows = list(csv.DictReader(fh))
        rows = [x for x in rows if None not in x.values() and x.get("verdict")]   # a row cut short by the copy is not a check
        ok_n = sum(1 for x in rows if x.get("armed") == "1" and x.get("state") == "NORMAL" and x.get("verdict") == "PASS")
        out.append((ok_n >= 1000, f"clean drive was checked: {ok_n} armed NORMAL checks passed in checks.csv (want >= 1000)"))
        # ... and nothing else once armed: every check from the first non-WAIT row on is an armed NORMAL PASS (the pre-arm
        # WAIT rows before the first true frame are the boot warm-up the bench also excludes)
        first = next((i for i, x in enumerate(rows) if x.get("state") != "WAIT"), len(rows))
        other = [x for x in rows[first:] if not (x.get("armed") == "1" and x.get("state") == "NORMAL" and x.get("verdict") == "PASS")]
        out.append((not other, f"clean drive after arming: {len(other)} checks other than an armed NORMAL PASS (want 0)"
                               + (f", first: {other[0].get('state')}/{other[0].get('verdict')} at {other[0].get('t_cap_start_ns')}" if other else "")))
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
    width = None
    if len(argv) == 5 and argv[3] == "--width" and argv[4].isdigit():
        width, argv = int(argv[4]), argv[:3]
    if len(argv) != 3 or argv[2] not in [*EXPECT, "none"]:
        print(__doc__)
        return 2
    run, cls = Path(argv[1]), argv[2]
    res = check(run, cls, width)
    for ok, msg in res:
        print(f"{'PASS' if ok else 'FAIL'} {cls}: {msg}")
    for w in WARNINGS:
        print(f"WARN {cls}: {w}")
    return 0 if all(ok for ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Summarise one P1a spike run directory (scripts/spike.sh output) into SUMMARY.md + PNGs.

    tools/spike_report.py evidence/spike/<run-id>

Reads g1.txt (RESULT lines), g4-timer.csv, g4-readback.csv, g3.txt, g2.txt; converts every *.ppm to *.png
(and deletes the ppm); writes SUMMARY.md with each gate's measured numbers and verdict against the bar in
the project spec. Percentiles are nearest-rank. Every timing is QEMU (TCG), not hardware timing.
"""
import csv
import math
import re
import struct
import sys
import zlib
from pathlib import Path


def pct(vals, p):
    v = sorted(vals)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def ppm_to_png(src: Path) -> Path:
    data = src.read_bytes()
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if not m:
        raise ValueError(f"not a P6 ppm: {src}")
    w, h = int(m.group(1)), int(m.group(2))
    px = data[m.end():]
    raw = b"".join(b"\x00" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    out = src.with_suffix(".png")
    out.write_bytes(png)
    src.unlink()
    return out


def parse_results(text: str):
    rows = []
    for line in text.splitlines():
        if line.startswith("RESULT "):
            rows.append(dict(kv.split("=", 1) for kv in line[7:].split() if "=" in kv))
    return rows


def main(d: Path) -> int:
    for p in sorted(d.glob("*.ppm")):
        ppm_to_png(p)
    out = [f"# P1a spike run `{d.name}` — QEMU (TCG), x86_64 QSTI guest", ""]
    verdicts = {}

    g2 = (d / "g2.txt").read_text() if (d / "g2.txt").exists() else ""
    verdicts["G2 (qcc binary runs; headless)"] = "PASS" if "TT-G2 hello from QNX 8.0.0" in g2 else "FAIL"
    out += ["## G2 — qcc-built binary runs on the target", "", "```", g2.strip(), "```", ""]

    res = parse_results((d / "g1.txt").read_text()) if (d / "g1.txt").exists() else []
    by = {(r.get("route"), r.get("tag")): r for r in res}
    out += ["## G1 — Screen read-back", "",
            "| route | tag | uid | status | mismatching px | refused with | read ms |", "|---|---|---|---|---|---|---|"]
    errname = {"1": "EPERM", "13": "EACCES", "48": "ENOTSUP"}
    for r in res:
        ms = f"{int(r['read_ns']) / 1e6:.2f}" if "read_ns" in r else "—"
        mm = r.get("mismatch_px", "—")
        ref = errname.get(r["errno"], "errno " + r["errno"]) if "errno" in r else r.get("reason", "—")
        status = "refused" if "errno" in r else r.get("status")
        out.append(f"| {r.get('route')} | {r.get('tag')} | {r.get('uid')} | {status} | {mm} | {ref} | {ms} |")
    plain_d = by.get(("display", "plain"), {}).get("status")
    plain_w = by.get(("window", "plain"), {}).get("status")
    cov_d = by.get(("display", "covered"), {})
    cov_w = by.get(("window", "covered"), {}).get("status")
    composed = cov_d.get("status") == "DIFF" and cov_d.get("mismatch_px") == "40000"
    if plain_d == "EXACT" and composed:
        verdicts["G1"] = "PASS (G1-FULL)"
    elif plain_w == "EXACT":
        verdicts["G1"] = "PASS (G1-WINDOW only)"
    else:
        verdicts["G1"] = "FAIL"
    verdicts["S-OCC"] = "PASS" if cov_w == "EXACT" else "FAIL"
    uid_rows = [r for r in res if r.get("route") == "window" and r.get("tag", "").startswith("uid1001")]
    verdicts["S-UID"] = "PASS" if uid_rows and uid_rows[0].get("status") == "EXACT" else "FAIL"
    out += ["", f"Composition check: the top-z cover over the right half changed {cov_d.get('mismatch_px', '?')} of 80000 px "
            "in the display read (expected 40000 = the covered half); the window read under the cover stayed "
            f"{cov_w}.", ""]

    if (d / "g4-timer.csv").exists():
        err = [abs(int(r["err_ns"])) for r in csv.DictReader((d / "g4-timer.csv").read_text().splitlines())]
        rb = list(csv.DictReader((d / "g4-readback.csv").read_text().splitlines()))
        chk = [int(r["check_ns"]) for r in rb]
        t95, c95 = pct(err, 95) / 1e6, pct(chk, 95) / 1e6
        verdicts["G4"] = "PASS" if t95 <= 5 and c95 <= 10 else "FAIL"
        out += ["## G4 — timing on this QEMU (TCG) target", "",
                "| measure | n | p50 ms | p95 ms | max ms | bar |", "|---|---|---|---|---|---|",
                f"| 20 ms timer pulse, abs error | {len(err)} | {pct(err, 50) / 1e6:.2f} | {t95:.2f} | {max(err) / 1e6:.2f} | p95 ≤ 5 |",
                f"| one check = 3 × screen_read_display | {len(chk)} | {pct(chk, 50) / 1e6:.2f} | {c95:.2f} | {max(chk) / 1e6:.2f} | p95 ≤ 10 |"]
        for k, name in (("lamp_ns", "lamp row 1280×64"), ("digit_ns", "digit block 360×336"), ("tick_ns", "tick 8×8")):
            v = [int(r[k]) for r in rb]
            out.append(f"| — {name} | {len(v)} | {pct(v, 50) / 1e6:.2f} | {pct(v, 95) / 1e6:.2f} | {max(v) / 1e6:.2f} | |")
        out += [""]

    g3 = (d / "g3.txt").read_text() if (d / "g3.txt").exists() else ""
    pushed = re.search(r"policy id:(?!0\b)\w+", g3)
    ok = (pushed and re.search(r"type=monitor_t\(id \d+\) -> \S*v\.chan: ConnectAttach OK", g3)
          and re.search(r"type=monitor_t\(id \d+\) -> \S*r\.chan: ConnectAttach DENIED errno=1", g3))
    drop_denied = re.search(r"drop to uid 1001[^\n]*\n(?:[^\n]*\n)??setuid: Operation not permitted", g3)
    verdicts["G3 uid drop limited"] = "PASS" if drop_denied else "FAIL"
    verdicts["G3 (minimal)"] = "PASS" if ok else "FAIL"
    out += ["## G3 — secpol least privilege (minimal: types + channel connect + uid drop)", "", "```", g3.strip(), "```", ""]

    out[1:1] = ["", "| gate | verdict |", "|---|---|"] + [f"| {k} | {v} |" for k, v in verdicts.items()] + [""]
    (d / "SUMMARY.md").write_text("\n".join(out) + "\n")
    print("\n".join(f"{k}: {v}" for k, v in verdicts.items()))
    return 0 if all(v.startswith("PASS") for v in verdicts.values()) else 3


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))

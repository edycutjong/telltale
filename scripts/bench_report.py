#!/usr/bin/env python3
"""evidence/report.html: one self-contained page (stdlib only, inline SVG) from the committed raw rows.

  bench_report.py [--out evidence/report.html]

Every number is computed here from evidence/*.csv by scripts/bench.py (nothing is typed in by hand), and the page says on
its first line that all timing is QEMU (TCG), not hardware timing. A class with no rows is simply absent.
"""
from __future__ import annotations

import argparse
import csv
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402

EV = ROOT / "evidence"
COLORS = {"FREEZE": "#d9534f", "BLANK": "#5b8def", "STALE": "#e0a030", "STARVE": "#8e6bd1", "CRASH": "#3c9d6b", "MISDRAW": "#c0568f"}


def rows_of(name: str) -> list[dict]:
    p = EV / name
    if not p.exists():
        return []
    return list(csv.DictReader(ln for ln in p.read_text().splitlines() if not ln.startswith("#")))


def detect_ms(rows: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for r in rows:
        if r.get("missed") == "0" and r.get("t_detect_ns") and r.get("t_inject_ns"):
            out.setdefault(r["class"], []).append((int(r["t_detect_ns"]) - int(r["t_inject_ns"])) / 1e6)
    return out


def strip_plot(data: dict[str, list[float]], title: str, bound: float | None = None) -> str:
    """One horizontal strip per class: every detection as a dot (nothing hidden), p50 and p95 ticks."""
    if not data:
        return ""
    xmax = max(max(v) for v in data.values()) * 1.08
    xmax = max(xmax, (bound or 0) * 1.05, 50)
    w, left, rowh = 760, 90, 34
    h = rowh * len(data) + 52
    def sx(v):
        return left + (w - left - 20) * v / xmax
    s = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(title)}" class="plot"><title>{html.escape(title)}</title>']
    for i, (cls, vs) in enumerate(sorted(data.items())):
        y = 26 + i * rowh
        col = COLORS.get(cls, "#888")
        s.append(f'<text x="{left - 10}" y="{y + 4}" text-anchor="end" class="lbl">{cls}</text><line x1="{left}" x2="{w - 20}" y1="{y}" y2="{y}" class="axis"/>')
        for j, v in enumerate(sorted(vs)):
            jitter = ((j * 7) % 9 - 4) * 1.6
            s.append(f'<circle cx="{sx(v):.1f}" cy="{y + jitter:.1f}" r="2.6" fill="{col}" fill-opacity=".55"/>')
        for q, dash in ((50, ""), (95, "3 2")):
            qv = bench.percentile(vs, q)
            s.append(f'<line x1="{sx(qv):.1f}" x2="{sx(qv):.1f}" y1="{y - 13}" y2="{y + 13}" stroke="{col}" stroke-width="2" stroke-dasharray="{dash}"/>')
    base = 26 + len(data) * rowh - 8
    step = 50 if xmax < 400 else 100
    t = 0
    while t <= xmax:
        s.append(f'<text x="{sx(t):.1f}" y="{base + 18}" text-anchor="middle" class="tick">{t}</text>')
        t += step
    if bound:
        s.append(f'<line x1="{sx(bound):.1f}" x2="{sx(bound):.1f}" y1="8" y2="{base}" class="bound"/><text x="{sx(bound) + 4:.1f}" y="16" class="tick">formula bound {bound:.0f} ms</text>')
    s.append(f'<text x="{w - 20}" y="{h - 4}" text-anchor="end" class="tick">detect − inject, ms · solid tick = p50 · dashed = p95 · QEMU (TCG)</text></svg>')
    return "".join(s)


def table(header: list[str], body: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in header)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"


def f1(v) -> str:
    return "–" if v is None else f"{v:.1f}"


def faults_section(title: str, rows: list[dict], bound: float | None = None, note: str = "") -> str:
    if not rows:
        return ""
    res = bench.analyze_faults(rows)
    body = []
    for cls, c in res["classes"].items():
        d, s, f = c["detect_ms"], c["safe_layer_recovery_ms"], c["full_recovery_ms"]
        body.append([cls, c["total"], c["misses"], f"{f1(d['p50'])} / {f1(d['p95'])} / {f1(d['max'])}",
                     f"{f1(s['p50'])} / {f1(s['p95'])}", f"{f1(f['p50'])} / {f1(f['p95'])}"])
    return (f"<h2>{html.escape(title)}</h2>{note}"
            + table(["class", "injections", "misses", "detect p50 / p95 / max (ms)", "safe layer p50 / p95 (ms)", "full recovery p50 / p95 (ms)"], body)
            + strip_plot(detect_ms(rows), title + " — detection time", bound)
            + f'<p class="small">problem rows (failed / inconsistent / missing stamps): {res["problem_rows"]}</p>')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(EV / "report.html"))
    a = ap.parse_args()
    parts = []
    cp = EV / "bench-clean.csv"
    if cp.exists():
        t = bench.load([str(cp)], bench.CHECK_COLUMNS)
        clean = bench.analyze_clean(t.rows, t.meta.get("accel", "tcg"))
        rb = clean["readback_check_us"]
        parts.append("<h2>B3 clean replay + B5 read-back cost</h2>" + table(
            ["checks", "duration (s)", "false positives", "read-back p50 / p95 / max (µs)"],
            [[clean["checks"], clean["duration_s"], clean["false_positives"], f"{f1(rb['p50'])} / {f1(rb['p95'])} / {f1(rb['max'])}"]]))
        bound = 151 + 20 + (rb["p95"] or 0) / 1000 + 20
    else:
        bound = None
    b1 = rows_of("bench-faults.csv")
    note = (f'<p class="small">Pre-registered formula bound for tick-detected classes: p95 ≤ tick_stall_ms (151) + period (20) + read-back p95 + 20 ms = '
            f'{bound:.0f} ms. STALE needs the truth to move, so it carries W = 100 ms by construction (target W + 100 = 200 ms).</p>' if bound else "")
    parts.insert(0, faults_section("B1 fault bench", b1, bound, note))
    ab = rows_of("bench-ablation.csv")
    if ab:
        res = bench.analyze_ablation(ab)
        body = [[c, v["n"], f"{v['median_s']:.2f}", f"{v['min_s']:.2f}", f"{v['max_s']:.2f}"] for c, v in res["classes"].items()]
        parts.insert(1, "<h2>B2 ablation — monitor off</h2><p>Seconds of the 30-s window the display showed a wrong value or no valid speed.</p>"
                     + table(["class", "runs", "median s wrong", "min", "max"], body)
                     + f'<p class="small">all {res["runs"]} runs: median {res["median_s"]:.2f} s, min {res["min_s"]:.2f} s; still wrong at the end of the window: '
                       f'{res["wrong_at_end"]}/{res["runs"]}; monitor incidents while disarmed: {res["monitor_acted_incidents"]}.</p>')
    parts.append(faults_section("B6 crash class (reported separately)", rows_of("bench-crash.csv")))
    parts.append(faults_section("B7e MISDRAW — the case only a pixel read catches", rows_of("bench-misdraw.csv")))
    per = rows_of("bench-period.csv")
    sweep = []
    for p in ("10", "20", "50"):
        rs = [r for r in (b1 if p == "20" else per) if r["period_ms"] == p and r["class"] == "FREEZE"]
        if rs:
            r = bench.analyze_faults(rs)["classes"]["FREEZE"]
            d = r["detect_ms"]
            sweep.append([p, r["total"], r["misses"], f"{f1(d['p50'])} / {f1(d['p95'])} / {f1(d['max'])}"])
    if sweep:
        parts.append("<h2>B4 period sweep (FREEZE × 50 per period)</h2>" + table(["period (ms)", "injections", "misses", "detect p50 / p95 / max (ms)"], sweep))
    css = ("body{font:15px/1.5 system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1b1f24}h1{font-size:1.5rem}h2{margin-top:2.2rem;font-size:1.15rem}"
           "table{border-collapse:collapse;width:100%;margin:.6rem 0}th,td{border-bottom:1px solid #d8dde3;padding:.35rem .5rem;text-align:left;font-variant-numeric:tabular-nums}"
           "th{font-weight:600;background:#f3f5f7}.small{font-size:.85rem;color:#4a5560}.plot{width:100%;height:auto;margin:.4rem 0}.lbl{font:600 12px system-ui}"
           ".tick{font:11px system-ui;fill:#4a5560}.axis{stroke:#d8dde3}.bound{stroke:#222;stroke-dasharray:4 3}.banner{background:#fff6e0;border:1px solid #e8cf8a;padding:.5rem .8rem;border-radius:6px}")
    out = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Telltale bench report</title><style>{css}</style></head><body>'
           '<h1>Telltale bench report</h1><p class="banner"><b>All timing is QEMU (TCG) timing on an x86_64 QNX 8.0 guest, not hardware timing.</b> '
           'Generated by scripts/bench_report.py from the raw rows in evidence/*.csv; every row names the git SHA of the code that ran.</p>'
           + "".join(p for p in parts if p) + "</body></html>")
    Path(a.out).write_text(out)
    print(f"wrote {a.out} ({len(out) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

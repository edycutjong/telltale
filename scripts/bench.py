#!/usr/bin/env python3
"""Host-side analysis of Telltale bench CSVs (stdlib only).

The measurement itself happens on the QNX target (`ttbench`); this script only
reads the raw rows it committed to evidence/ and reports p50/p95/max.

  bench.py faults evidence/bench-faults-<date>.csv   # B1 / B4 / B6 / B7 rows
  bench.py clean  evidence/bench-clean-<date>.csv    # B3 false-positive run + B5 read-back cost

Rules this script enforces (so a table cannot hide a bad row):
  * a fault row with no t_inject is FAILED, never estimated;
  * a fault row is a MISS when t_detect is empty or later than 2 s after t_inject,
    and must agree with the row's own `missed` column (else it is INCONSISTENT);
  * a detected row counts only if its `reason` (the incident's verdict) is a display-fault verdict
    for the injected class (FAULT_VERDICTS, the same sets as scripts/check_run.py EXPECT): a TRUTH_LOST
    or blank reason claims a detection the monitor did not make, and the row is INCONSISTENT;
  * all rows of one report must share one configuration
    (accel, tier, smp, runmask, period_ms) unless --allow-mixed is given;
  * a detected row must be time-ordered (t_detect >= t_inject, recovery stamps >= t_detect);
  * a detected row with no t_safe_visible (or no t_true, except STARVE, whose renderer is
    never restarted) is counted per class as a missing recovery stamp, and every recovery
    column shows its own n, so a smaller n can never hide behind the row count;
  * classes passed with --detect-only (e.g. B7d, Screen SIGSTOP: no recovery claimed) are
    exempt from the recovery-stamp rule;
  * a clean report covers exactly one run (one file) and needs its accel, from --accel or a
    "# accel=<tcg|hvf|pi5>" comment line, so B3/B5 numbers carry the same QEMU label;
  * --strict exits 3 when any row is failed, inconsistent or missing a recovery stamp;
  * percentiles are nearest-rank (p95 of N values = the ceil(0.95*N)-th smallest);
  * QEMU accelerators (tcg, hvf) are labelled "QEMU timing, not hardware timing".
Lines starting with '#' are comments; a file whose comments contain
"SYNTHETIC" is reported with a SYNTHETIC banner (test fixtures only).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

FAULT_COLUMNS = (
    "run_id,git_sha,accel,tier,smp,runmask,period_ms,seed,class,t_command_ns,t_inject_ns,"
    "t_diverge_ns,t_detect_ns,reason,cause,t_safe_visible_ns,t_restart_ns,t_true_ns,shown,"
    "truth,missed,hog_prio,exposure_ms,tick_stall_ms"
).split(",")

CHECK_COLUMNS = (
    "t_cap_start_ns,t_cap_end_ns,check_us,state,armed,verdict,decoded,truth_min,truth_max,tick,marker"
).split(",")

CONFIG_KEYS = ("accel", "tier", "smp", "runmask", "period_ms")
# verdicts that count as detecting each injected class (= scripts/check_run.py EXPECT); any other class: any display verdict
DISPLAY_VERDICTS = frozenset({"TICK_STALL", "VALUE_MISMATCH", "BLANK", "UNREADABLE"})
FAULT_VERDICTS = {
    "FREEZE": frozenset({"TICK_STALL", "VALUE_MISMATCH"}),            # mismatch first if the speed moves
    "STARVE": frozenset({"TICK_STALL", "VALUE_MISMATCH"}),
    "BLANK": frozenset({"BLANK", "UNREADABLE", "VALUE_MISMATCH"}),    # a read can straddle the cleared frame
    "STALE": frozenset({"VALUE_MISMATCH"}),
    "MISDRAW": frozenset({"VALUE_MISMATCH"}),
    "CRASH": DISPLAY_VERDICTS,
}


def detects(cls: str, verdict: str) -> bool:
    """True if an incident with this verdict is a detection of an injected fault of class cls."""
    return (verdict or "").strip() in FAULT_VERDICTS.get(cls, DISPLAY_VERDICTS)
QEMU_ACCELS = {"tcg", "hvf"}
MISS_NS = 2_000_000_000  # "missed" = no detection within 2 s
SYNTHETIC_MARK = "SYNTHETIC"


class BenchError(ValueError):
    """Input that must not be summarised (wrong schema, mixed configuration)."""


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    ordered = sorted(values)
    rank = math.ceil(p / 100.0 * len(ordered))
    return ordered[rank - 1]


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": max(values) if values else None,
    }


@dataclass
class Table:
    rows: list[dict]
    synthetic: bool
    source: str
    meta: dict = field(default_factory=dict)


def read_table(text: str, columns: list[str], source: str = "<input>") -> Table:
    lines = text.splitlines()
    comments = [ln for ln in lines if ln.lstrip().startswith("#")]
    body = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not body:
        raise BenchError(f"{source}: no header row")
    reader = csv.DictReader(io.StringIO("\n".join(body)))
    header = reader.fieldnames or []
    missing = [c for c in columns if c not in header]
    if missing:
        raise BenchError(f"{source}: missing columns {missing}")
    rows = list(reader)
    if not rows:
        raise BenchError(f"{source}: header only, no data rows")
    synthetic = any(SYNTHETIC_MARK in c for c in comments)
    meta = {}
    for c in comments:
        k, sep, v = c.lstrip("# ").partition("=")
        if sep and k.strip().isidentifier():
            meta[k.strip()] = v.strip()
    return Table(rows=rows, synthetic=synthetic, source=source, meta=meta)


def load(paths: list[str], columns: list[str]) -> Table:
    rows: list[dict] = []
    synthetic = False
    for p in paths:
        t = read_table(Path(p).read_text(encoding="utf-8"), columns, source=p)
        rows.extend(t.rows)
        synthetic = synthetic or t.synthetic
    return Table(rows=rows, synthetic=synthetic, source=",".join(paths), meta=t.meta if len(paths) == 1 else {})


def _ns(row: dict, key: str) -> int | None:
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        raise BenchError(f"non-integer {key}={v!r}") from None


def _float(row: dict, key: str) -> float | None:
    v = (row.get(key) or "").strip()
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        raise BenchError(f"non-numeric {key}={v!r}") from None


def configs(rows: list[dict]) -> list[dict]:
    seen: list[dict] = []
    for r in rows:
        c = {k: (r.get(k) or "").strip() for k in CONFIG_KEYS}
        if c not in seen:
            seen.append(c)
    return seen


def check_single_config(rows: list[dict], allow_mixed: bool) -> list[dict]:
    cfgs = configs(rows)
    if len(cfgs) > 1 and not allow_mixed:
        raise BenchError(f"rows span {len(cfgs)} configurations {cfgs}; one report = one configuration")
    return cfgs


def timing_label(cfgs: list[dict]) -> str:
    accels = sorted({c["accel"] for c in cfgs})
    if accels and all(a in QEMU_ACCELS for a in accels):
        return f"QEMU timing (accel={'/'.join(accels)}), not hardware timing"
    return f"accel={'/'.join(accels) or 'unknown'}"


@dataclass
class ClassStats:
    total: int = 0
    failed: int = 0
    inconsistent: int = 0
    misses: int = 0
    missing_safe: int = 0
    missing_true: int = 0
    detect_ms: list[float] = field(default_factory=list)
    detect_from_diverge_ms: list[float] = field(default_factory=list)
    safe_ms: list[float] = field(default_factory=list)
    full_ms: list[float] = field(default_factory=list)


NO_RESTART_CLASSES = {"STARVE"}  # the renderer recovers when the hog ends; no t_restart / t_true expected


def analyze_faults(rows: list[dict], allow_mixed: bool = False, detect_only: tuple[str, ...] = ()) -> dict:
    cfgs = check_single_config(rows, allow_mixed)
    per: dict[str, ClassStats] = {}
    for r in rows:
        cls = (r.get("class") or "").strip() or "?"
        s = per.setdefault(cls, ClassStats())
        s.total += 1
        t_inject = _ns(r, "t_inject_ns")
        if t_inject is None:
            s.failed += 1  # missing effective-time stamp: the run failed, never estimated
            continue
        t_detect = _ns(r, "t_detect_ns")
        missed = t_detect is None or t_detect - t_inject > MISS_NS
        declared = (r.get("missed") or "").strip()
        if declared not in ("0", "1") or (declared == "1") != missed:
            s.inconsistent += 1
            continue
        if missed:
            s.misses += 1
            continue
        if not detects(cls, r.get("reason") or ""):
            s.inconsistent += 1  # the incident it names is not a display fault of this class (e.g. TRUTH_LOST)
            continue
        t_safe = _ns(r, "t_safe_visible_ns")
        t_true = _ns(r, "t_true_ns")
        t_restart = _ns(r, "t_restart_ns")
        if t_detect < t_inject or any(t is not None and t < t_detect for t in (t_safe, t_true, t_restart)):
            s.inconsistent += 1  # time runs backwards: a logging bug, not a measurement
            continue
        s.detect_ms.append((t_detect - t_inject) / 1e6)
        t_div = _ns(r, "t_diverge_ns")
        if t_div is not None:
            s.detect_from_diverge_ms.append((t_detect - t_div) / 1e6)
        if t_safe is None:
            if cls not in detect_only:
                s.missing_safe += 1
        else:
            s.safe_ms.append((t_safe - t_detect) / 1e6)
        if t_true is None:
            if cls not in detect_only and cls not in NO_RESTART_CLASSES:
                s.missing_true += 1
        else:
            s.full_ms.append((t_true - t_detect) / 1e6)
    classes = {
        cls: {
            "total": s.total,
            "failed": s.failed,
            "inconsistent": s.inconsistent,
            "misses": s.misses,
            "missing_safe_stamp": s.missing_safe,
            "missing_true_stamp": s.missing_true,
            "detect_ms": summarize(s.detect_ms),
            "detect_from_diverge_ms": summarize(s.detect_from_diverge_ms),
            "safe_layer_recovery_ms": summarize(s.safe_ms),
            "full_recovery_ms": summarize(s.full_ms),
        }
        for cls, s in sorted(per.items())
    }
    problems = sum(c["failed"] + c["inconsistent"] + c["missing_safe_stamp"] + c["missing_true_stamp"]
                   for c in classes.values())
    return {"kind": "faults", "configs": cfgs, "timing": timing_label(cfgs), "classes": classes,
            "problem_rows": problems}


def analyze_clean(rows: list[dict], accel: str) -> dict:
    """B3: any non-PASS verdict in the clean run is a false positive (armed or not;
    the armed-only count is reported beside it). B5: check_us."""
    armed = [r for r in rows if (r.get("armed") or "").strip() == "1"]
    fps = [r for r in rows if (r.get("verdict") or "").strip() != "PASS"]
    fps_armed = [r for r in fps if (r.get("armed") or "").strip() == "1"]
    check_us = [v for v in (_float(r, "check_us") for r in rows) if v is not None]
    starts = [_ns(r, "t_cap_start_ns") for r in rows]
    starts = [s for s in starts if s is not None]
    duration_s = (max(starts) - min(starts)) / 1e9 if len(starts) > 1 else 0.0
    return {
        "kind": "clean",
        "timing": timing_label([{"accel": accel}]),
        "checks": len(rows),
        "armed_checks": len(armed),
        "false_positives": len(fps),
        "false_positives_armed": len(fps_armed),
        "duration_s": round(duration_s, 3),
        "false_positives_per_30min": round(len(fps) / duration_s * 1800, 3) if duration_s > 0 else None,
        "readback_check_us": summarize(check_us),
    }


def _fmt(v: float | None) -> str:
    return "–" if v is None else f"{v:.1f}"


def render_faults(result: dict, synthetic: bool) -> str:
    out = []
    if synthetic:
        out.append("**SYNTHETIC TEST FIXTURE — not a measurement**\n")
    out.append(f"Timing: {result['timing']}\n")
    out.append("| class | rows | failed | inconsistent | misses | detect n · p50 / p95 / max (ms) "
               "| safe-layer n · p50 / p95 (ms) | full n · p50 / p95 (ms) |")
    out.append("|---|---|---|---|---|---|---|---|")
    for cls, c in result["classes"].items():
        d, sf, fu = c["detect_ms"], c["safe_layer_recovery_ms"], c["full_recovery_ms"]
        out.append(
            f"| {cls} | {c['total']} | {c['failed']} | {c['inconsistent']} | {c['misses']} | "
            f"{d['n']} · {_fmt(d['p50'])} / {_fmt(d['p95'])} / {_fmt(d['max'])} | "
            f"{sf['n']} · {_fmt(sf['p50'])} / {_fmt(sf['p95'])} | {fu['n']} · {_fmt(fu['p50'])} / {_fmt(fu['p95'])} |"
        )
    out.append(f"\nproblem rows: {result['problem_rows']} (failed + inconsistent + missing recovery stamps)")
    return "\n".join(out) + "\n"


def render_clean(result: dict, synthetic: bool) -> str:
    out = []
    if synthetic:
        out.append("**SYNTHETIC TEST FIXTURE — not a measurement**\n")
    rb = result["readback_check_us"]
    rate = result["false_positives_per_30min"]
    out.append(f"Timing: {result['timing']}\n")
    out.append(
        f"checks {result['checks']} (armed {result['armed_checks']}) over {result['duration_s']} s · "
        f"false positives {result['false_positives']} (armed {result['false_positives_armed']}, "
        f"per 30 min {'n/a (run too short)' if rate is None else rate}) · read-back µs p50/p95/max "
        f"{_fmt(rb['p50'])} / {_fmt(rb['p95'])} / {_fmt(rb['max'])}"
    )
    return "\n".join(out) + "\n"


ABLATION_COLUMNS = ["run_id", "git_sha", "accel", "class", "window_s", "seconds_wrong", "wrong_at_end", "monitor_acted"]
ABLATION_BAR_S = 25.0  # spec.md: median >= 25 s of the 30-s window, per run, all runs


def analyze_ablation(rows: list[dict]) -> dict:
    """B2: monitor disarmed. Per class: seconds of the window the display showed a wrong value / no valid speed."""
    per: dict[str, list[float]] = {}
    bad = acted = still_wrong = 0
    for r in rows:
        v = _float(r, "seconds_wrong")
        if v is None:
            bad += 1  # no measurement for this run: counted, never estimated
            continue
        per.setdefault((r.get("class") or "?").strip(), []).append(v)
        acted += int((r.get("monitor_acted") or "0") or 0)
        still_wrong += int((r.get("wrong_at_end") or "0") or 0)
    allv = [v for vs in per.values() for v in vs]
    classes = {c: {"n": len(vs), "median_s": percentile(vs, 50), "min_s": min(vs), "max_s": max(vs)}
               for c, vs in sorted(per.items())}
    return {"kind": "ablation", "classes": classes, "runs": len(allv), "missing_rows": bad,
            "median_s": percentile(allv, 50) if allv else None, "min_s": min(allv) if allv else None,
            "runs_below_bar": sum(1 for v in allv if v < ABLATION_BAR_S), "monitor_acted_incidents": acted,
            "wrong_at_end": still_wrong, "window_s": (rows[0].get("window_s") if rows else None),
            "problem_rows": bad}


def render_ablation(res: dict) -> str:
    out = [f"Monitor disarmed (observe-only); {res['runs']} runs, window {res['window_s']} s. QEMU timing, not hardware timing.\n",
           "| class | runs | median s wrong | min | max |", "|---|---|---|---|---|"]
    for c, v in res["classes"].items():
        out.append(f"| {c} | {v['n']} | {v['median_s']:.2f} | {v['min_s']:.2f} | {v['max_s']:.2f} |")
    out.append(f"\nall runs: median {res['median_s']:.2f} s, min {res['min_s']:.2f} s; runs below the {ABLATION_BAR_S:.0f}-s bar: "
               f"{res['runs_below_bar']}; still wrong at the end of the window: {res['wrong_at_end']}/{res['runs']}; "
               f"monitor incidents while disarmed: {res['monitor_acted_incidents']}; rows without a measurement: {res['missing_rows']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["faults", "clean", "ablation"])
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--strict", action="store_true",
                    help="faults: exit 3 if any row is failed, inconsistent or missing a recovery stamp")
    ap.add_argument("--detect-only", action="append", default=[], metavar="CLASS",
                    help="faults: class with no recovery claimed (repeatable), e.g. B7d")
    ap.add_argument("--accel", choices=["tcg", "hvf", "pi5"], help="clean: accelerator of the run")
    ap.add_argument("--allow-mixed", action="store_true", help="faults: allow rows from several configurations")
    a = ap.parse_args(argv)
    try:
        if a.kind == "faults":
            t = load(a.csv, FAULT_COLUMNS)
            res = analyze_faults(t.rows, allow_mixed=a.allow_mixed, detect_only=tuple(a.detect_only))
            text = render_faults(res, t.synthetic)
        elif a.kind == "ablation":
            t = load(a.csv, ABLATION_COLUMNS)
            res = analyze_ablation(t.rows)
            text = render_ablation(res)
        else:
            if len(a.csv) != 1:
                raise BenchError("clean: one run per report (exactly one CSV)")
            t = load(a.csv, CHECK_COLUMNS)
            accel = a.accel or t.meta.get("accel")
            if accel not in ("tcg", "hvf", "pi5"):
                raise BenchError("clean: accel unknown; pass --accel or add '# accel=<tcg|hvf|pi5>' to the CSV")
            res = analyze_clean(t.rows, accel)
            text = render_clean(res, t.synthetic)
    except BenchError as e:
        print(f"bench.py: {e}", file=sys.stderr)
        return 2
    res["synthetic"] = t.synthetic
    sys.stdout.write(json.dumps(res, indent=2) + "\n" if a.json else text)
    if a.strict and res.get("problem_rows", 0) > 0:
        print(f"bench.py: --strict: {res['problem_rows']} problem rows", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())

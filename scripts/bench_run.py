#!/usr/bin/env python3
"""Host-side driver for the on-target Telltale bench (stdlib only). Every number comes from the QNX guest
(QEMU, TCG); this script only schedules runs, copies the raw logs back and turns them into rows.

  bench_run.py windows                        list the speed-changing windows of data/trace-bench.csv (the K of the census)
  bench_run.py faults  [--n 50] ...           B1: <n> injections per class (FREEZE BLANK STALE STARVE), seed 42, shuffled
  bench_run.py crash|misdraw|period|ablation  B6 / B7e / B4 / B2 (see --help)
  bench_run.py clean   [--seconds 1800]       B3: clean replay, per-check CSV (also gives B5 read-back cost)

Design (one run = one fresh stack, so a run can never inherit state from the one before):
  * slice: rows [onset-3 s, onset+25 s] of the real comma2k19 bench trace, re-based to t=0; `scenario.sh` replays it
    with TT_TRACE; the fault fires at trace second 3.2 (200 ms into the speed-changing window).
  * the fault time `t_inject` is stamped on the target by the component where the fault takes effect
    (cluster-bench.csv, or the hog's first busy-loop stamp for STARVE) -- never by this script;
  * rows are appended as they finish and the run resumes where it stopped (`--out` file is the state).
Rows carry git_sha: the bench refuses to start on a dirty build/ tree (the SHA must name the code that ran).
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "data" / "trace-bench.csv"
STOPS = ROOT / "data" / "trace-bench-stops.csv"
CLASSES = ["FREEZE", "BLANK", "STALE", "STARVE"]
INJECT_ARG = {"FREEZE": "freeze", "BLANK": "blank", "STALE": "stale", "MISDRAW": "misdraw", "CRASH": "crash"}
PRE_S, POST_S, AT_S = 3.0, 25.0, 3.2
FAULT_COLUMNS = (
    "run_id,git_sha,accel,tier,smp,runmask,period_ms,seed,class,t_command_ns,t_inject_ns,"
    "t_diverge_ns,t_detect_ns,reason,cause,t_safe_visible_ns,t_restart_ns,t_true_ns,shown,"
    "truth,missed,hog_prio,exposure_ms,tick_stall_ms,incidents,pre_inject_incidents,window_onset_s,host_load1"
).split(",")


def read_trace() -> list[tuple[float, float]]:
    with open(TRACE) as f:
        r = csv.reader(f)
        next(r)
        return [(float(a), float(b)) for a, b in r]


def windows(trace: list[tuple[float, float]], thr_kmh: float = 1.0) -> list[float]:
    """Onset (s) of each distinct speed-changing window: |v(t+100 ms) - v(t)| >= thr on a 100 ms grid; consecutive
    qualifying steps form one window, windows closer than 1 s are merged (same rule as tools/trace_from_comma.py)."""
    import bisect
    ts = [t for t, _ in trace]
    vs = [v * 3.6 for _, v in trace]

    def at(t: float) -> float:
        i = min(max(bisect.bisect_left(ts, t), 1), len(ts) - 1)
        a, b = i - 1, i
        return vs[a] if ts[b] == ts[a] else vs[a] + (vs[b] - vs[a]) * (t - ts[a]) / (ts[b] - ts[a])

    onsets: list[float] = []
    last_end = -1e9
    t = ts[0]
    in_win = False
    while t + 0.1 <= ts[-1]:
        q = abs(at(t + 0.1) - at(t)) >= thr_kmh
        if q:
            if not in_win and t - last_end >= 1.0:
                onsets.append(round(t, 1))
            in_win = True
            last_end = t + 0.1
        else:
            in_win = False
        t = round(t + 0.1, 3)
    return onsets


def stale_observable(trace: list[tuple[float, float]], onset: float, min_delta_kmh: float = 2.0) -> bool:
    """A STALE injection (data thread stops taking samples at onset + AT_S - PRE_S) can only be seen by a value check if the
    true speed then moves: |v(t + 1 s) - v(t)| >= 2 km/h at the injection instant t. Windows that are only the last 1-2 km/h
    step into a standstill fail this (the held value equals the truth) and are not used for STALE. Computed from the
    trace alone, before any run (rule recorded in progress.md after the first attempt showed the defect, 2026-11-15)."""
    import bisect
    ts = [t for t, _ in trace]

    def kmh(t: float) -> float:
        return trace[min(bisect.bisect_left(ts, t), len(ts) - 1)][1] * 3.6
    t = onset + (AT_S - PRE_S)
    return abs(kmh(t + 1.0) - kmh(t)) >= min_delta_kmh


def sh(cmd: list[str], env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(cmd, cwd=ROOT, env=e, capture_output=True, text=True, check=check)


def git_sha() -> str:
    dirty = sh(["git", "status", "--porcelain", "--", ".", ":!evidence"], check=False).stdout.strip()
    if dirty and not os.environ.get("TT_BENCH_DIRTY_OK"):
        sys.exit("bench_run: build/ has uncommitted changes outside evidence/ -- commit first (rows name the code that ran)\n" + dirty)
    return sh(["git", "rev-parse", "--short", "HEAD"]).stdout.strip()


def load1() -> str:
    try:
        return os.popen("sysctl -n vm.loadavg").read().split()[1]
    except Exception:
        return ""


def conf_value(key: str, extra: str = "") -> str:
    val = ""
    for line in (ROOT / "scripts/target/telltale.conf").read_text().splitlines() + extra.split("\\n"):
        line = line.split("#")[0].strip()
        if line.startswith(key + "="):
            val = line.split("=", 1)[1].strip()
    return val


def write_slice(path: Path, trace: list[tuple[float, float]], t0: float, t1: float, hold_to: float | None = None) -> None:
    rows = [(t - t0, v) for t, v in trace if t0 <= t <= t1]
    if hold_to is not None and rows:   # parked car: hold the last sample (vehfeed also repeats the last row)
        pass
    with open(path, "w") as f:
        f.write("t_s,speed_mps\n")
        for t, v in rows:
            f.write(f"{t:.3f},{v:.4f}\n")


def trace_kmh_at(rows: list[tuple[float, float]], t: float) -> float:
    best = min(rows, key=lambda r: abs(r[0] - t))
    return best[1] * 3.6


def first_int(path: Path, key: str) -> int | None:
    import re
    if path.exists():
        m = re.search(key + r"=(\d+)", path.read_text())
        if m:
            return int(m.group(1))
    return None


def read_incidents(run: Path) -> list[dict]:
    cols = ["id", "verdict", "cause", "shown", "truth", "t_last_verified_ns", "t_detect_ns", "caught_ms",
            "t_safe_visible_ns", "t_kill_ns", "t_register_ns", "t_true_ns", "restarts", "exposure_ms",
            "renderer_pid", "new_pid", "lamps_shown", "lamps_truth"]
    p = run / "incidents.csv"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip() and not line.startswith("id,"):
                out.append(dict(zip(cols, line.split(","))))
    return out


def blank_zero(v: str | None) -> str:
    return "" if v in (None, "", "0", "-1") else v


def make_row(run: Path, run_id: str, cls: str, meta: dict) -> dict:
    """One fault row from one run directory (incidents.csv, cluster-bench.csv, inject.txt | hog.log)."""
    row = {c: "" for c in FAULT_COLUMNS}
    row.update(run_id=run_id, git_sha=meta["sha"], accel="tcg", tier="G1-FULL", smp="4", runmask="0xf",
               period_ms=meta["period_ms"], seed=meta["seed"], **{"class": cls}, tick_stall_ms=meta["tick_stall_ms"],
               hog_prio=meta.get("hog_prio", ""), window_onset_s=meta.get("onset", ""), host_load1=meta.get("load1", ""))
    t_cmd = first_int(run / "inject.txt", "t_command_ns") or first_int(run / "hog.log", "t_hog_start_ns")
    t_inj = None
    if cls == "STARVE":
        t_inj = t_cmd
    else:
        bench = run / "cluster-bench.csv"
        if bench.exists():
            for line in bench.read_text().splitlines():
                f = line.split(",")
                if len(f) >= 4 and f[2] == cls:
                    t_inj = int(f[3])
                    break
    row["t_command_ns"] = t_cmd or ""
    row["t_inject_ns"] = t_inj or ""
    incs = read_incidents(run)
    pre = [i for i in incs if t_inj and int(i["t_detect_ns"]) < t_inj]
    post = [i for i in incs if not t_inj or int(i["t_detect_ns"]) >= t_inj]
    row["incidents"] = len(incs)
    row["pre_inject_incidents"] = len(pre)
    if t_inj is None:
        return row   # no effective-time stamp: bench.py counts it FAILED, never estimated
    inc = post[0] if post else None
    if inc is None or int(inc["t_detect_ns"]) - t_inj > 2_000_000_000:
        row["missed"] = "1"
    else:
        row["missed"] = "0"
        row.update(t_detect_ns=inc["t_detect_ns"], reason=inc["verdict"], cause=inc["cause"],
                   t_safe_visible_ns=blank_zero(inc["t_safe_visible_ns"]), t_restart_ns=blank_zero(inc["t_register_ns"]),
                   t_true_ns=blank_zero(inc["t_true_ns"]), shown=inc["shown"], truth=inc["truth"],
                   exposure_ms=inc["exposure_ms"])
    if cls == "BLANK":
        row["t_diverge_ns"] = t_inj
    elif cls in ("FREEZE", "STALE") and meta.get("slice"):
        tr = meta["slice"]
        import re
        m = re.search(r"trace_s=([\d.]+) speed_kmh=([\d.]+)", (run / "inject.txt").read_text()) if (run / "inject.txt").exists() else None
        if m:
            t_tr, shown = float(m.group(1)), float(m.group(2))
            for t, v in tr:
                if t >= t_tr and round(v * 3.6) != round(shown):
                    row["t_diverge_ns"] = t_inj + round((t - t_tr) * 1e9)   # derived offline from the trace
                    break
    return row


def append_row(out: Path, row: dict) -> None:
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, FAULT_COLUMNS)
        if new:
            f.write("# accel=tcg\n# QEMU (TCG) x86_64 guest on an Apple-silicon Mac: QEMU timing, not hardware timing. "
                    "t_diverge_ns is derived offline from the replayed trace; every other stamp is written on the target (CLOCK_MONOTONIC).\n")
            w.writeheader()
        w.writerow(row)


def done_ids(out: Path) -> set[str]:
    if not out.exists():
        return set()
    return {r["run_id"] for r in csv.DictReader(l for l in out.read_text().splitlines() if not l.startswith("#"))}


def keep_small(run: Path) -> None:
    """Drop the bulky per-check log of a fault run; the rows + incident/inject stamps stay (raw evidence)."""
    for name in ("checks.csv", "up.txt", "pidin.txt", "super.log", "vehfeed.log", "vehicled.log", "cluster.log"):
        (run / name).unlink(missing_ok=True)


def one_fault_run(tag: str, cls: str, onset: float, meta: dict, evroot: Path, extra_conf: str = "", settle: int = 8,
                  inject_name: str | None = None) -> Path:
    trace = meta["trace"]
    sl = Path("/tmp/tt-bench") / f"{tag}.csv"
    sl.parent.mkdir(exist_ok=True)
    write_slice(sl, trace, onset - PRE_S, onset + POST_S)
    meta["slice"] = [(t - (onset - PRE_S), v) for t, v in trace if onset - PRE_S <= t <= onset + POST_S]
    env = {"TT_TRACE": str(sl), "TT_HOLD": "3", "TT_EVROOT": str(evroot), "TT_RUN_ID": tag, "TT_NOCOLLECT": "1",
           "TT_CONF_EXTRA": extra_conf}
    if cls == "STARVE":
        env.update(TT_SCOPE="renderer", TT_HOG_MS="3000")
    elif inject_name:
        env["TT_INJECT"] = inject_name
    r = sh(["scripts/scenario.sh", cls.lower() if cls in ("FREEZE", "BLANK", "STALE", "STARVE", "CRASH", "MISDRAW") else cls,
            f"{AT_S}", str(settle)], env=env, check=False)
    return evroot / f"{tag}-{cls.lower()}"


def cmd_windows(a) -> None:
    w = windows(read_trace(), a.threshold)
    print(f"K={len(w)} windows at {a.threshold} km/h per 100 ms:", " ".join(str(x) for x in w))


def cmd_faults(a) -> None:
    trace = read_trace()
    onsets = [t for t in windows(trace, 1.0) if t > PRE_S + 1 and t < trace[-1][0] - POST_S - 1]
    sha = git_sha()
    classes = a.classes.split(",")
    sched = [c for c in classes for _ in range(a.n)]
    random.Random(a.seed).shuffle(sched)
    out = Path(a.out)
    evroot = Path(a.evroot)
    meta0 = {"sha": sha, "seed": a.seed, "period_ms": conf_value("period_ms", a.conf_extra) or "20",
             "tick_stall_ms": conf_value("tick_stall_ms", a.conf_extra), "trace": trace, "hog_prio": ""}
    have = done_ids(out)
    seen: dict[str, int] = {}
    stale_pool = [o for o in onsets if stale_observable(trace, o)]
    print(f"windows: {len(onsets)} (STALE-observable: {len(stale_pool)})", flush=True)
    for i, cls in enumerate(sched):
        tag = f"{a.tag}-{i + 1:03d}"
        pool = stale_pool if cls == "STALE" else onsets
        onset = pool[seen.get(cls, 0) % len(pool)]     # each class walks the windows in time order
        seen[cls] = seen.get(cls, 0) + 1
        if tag in have:
            continue
        meta = dict(meta0, onset=onset, load1=load1(), hog_prio="40" if cls == "STARVE" else "")
        run = one_fault_run(tag, cls, onset, meta, evroot, a.conf_extra, a.settle, INJECT_ARG.get(cls))
        row = make_row(run, tag, cls, meta)
        append_row(out, row)
        keep_small(run)
        print(f"{tag} {cls} onset={onset} missed={row['missed']} detect_ms="
              f"{(int(row['t_detect_ns']) - int(row['t_inject_ns'])) / 1e6 if row['t_detect_ns'] and row['t_inject_ns'] else '-'} "
              f"load1={meta['load1']}", flush=True)
        time.sleep(a.gap)


ABL_COLUMNS = ("run_id,git_sha,accel,class,seed,stop_t_s,inject_before_stop_s,t_inject_ns,window_s,seconds_wrong,"
               "checks_in_window,wrong_at_end,monitor_acted,host_load1").split(",")


def seconds_wrong(checks: Path, t_inj: int, window_s: float) -> tuple[float, int, int]:
    """Seconds of the window in which the glass showed a wrong value or no valid speed, from the per-check rows
    (monitor disarmed, observe-only): a check is 'wrong' when the decoded value is not a speed (blank, unreadable)
    or lies outside the truth window [truth_min, truth_max] the monitor derived from the real trace. Each check
    owns the time until the next check starts."""
    rows = []
    with open(checks, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                rows.append((int(r["t_cap_start_ns"]), int(r["decoded"]), int(r["truth_min"]), int(r["truth_max"]), r["state"]))
            except (ValueError, KeyError):
                continue
    t_end = t_inj + int(window_s * 1e9)
    wrong_ns, n, last_wrong = 0, 0, 0
    for (t, dec, lo, hi, st), nxt in zip(rows, rows[1:]):
        if t < t_inj or t >= t_end or lo < 0:
            continue
        n += 1
        w = dec < 0 or not (lo <= dec <= hi)
        last_wrong = int(w)
        if w:
            wrong_ns += min(nxt[0], t_end) - t
    return wrong_ns / 1e9, n, last_wrong


def cmd_ablation(a) -> None:
    import re
    trace = read_trace()
    stops = [r for r in csv.DictReader(open(STOPS))]
    sha = git_sha()
    sched = [c for c in CLASSES for _ in range(a.n)]
    rng = random.Random(a.seed)
    rng.shuffle(sched)
    out = Path(a.out)
    evroot = Path(a.evroot)
    have = done_ids(out)
    for i, cls in enumerate(sched):
        tag = f"{a.tag}-{i + 1:03d}"
        before = round(rng.uniform(1.0, 3.0), 2)     # drawn for every i so a resumed run keeps the same schedule
        st = stops[i % len(stops)]
        t_stop, stat = float(st["t_stop_s"]), float(st["stationary_s"])
        if tag in have:
            continue
        t0 = t_stop - before - AT_S
        t1 = t_stop + min(stat, a.window + 4)
        sl = Path("/tmp/tt-bench") / f"{tag}.csv"
        sl.parent.mkdir(exist_ok=True)
        write_slice(sl, trace, t0, t1)
        env = {"TT_TRACE": str(sl), "TT_HOLD": "3", "TT_EVROOT": str(evroot), "TT_RUN_ID": tag, "TT_DISARM": "1",
               "TT_CONF_EXTRA": "tick_stall_ms=" + conf_value("tick_stall_ms")}
        if cls == "STARVE":
            env.update(TT_SCOPE="renderer", TT_HOG_MS=str(int(a.window * 1000)))
        else:
            env["TT_INJECT"] = INJECT_ARG[cls]
        l1 = load1()
        sh(["scripts/scenario.sh", cls.lower(), f"{AT_S}", str(int(a.window + 3))], env=env, check=False)
        run = evroot / f"{tag}-{cls.lower()}"
        t_inj = first_int(run / "hog.log", "t_hog_start_ns") if cls == "STARVE" else None
        if cls != "STARVE" and (run / "cluster-bench.csv").exists():
            for line in (run / "cluster-bench.csv").read_text().splitlines():
                f = line.split(",")
                if len(f) >= 4 and f[2] == cls:
                    t_inj = int(f[3])
                    break
        row = {c: "" for c in ABL_COLUMNS}
        row.update(run_id=tag, git_sha=sha, accel="tcg", seed=a.seed, stop_t_s=t_stop, inject_before_stop_s=before,
                   window_s=a.window, host_load1=l1, **{"class": cls})
        checks = run / "checks.csv"
        if t_inj and checks.exists():
            sw, n, last = seconds_wrong(checks, t_inj, a.window)
            row.update(t_inject_ns=t_inj, seconds_wrong=f"{sw:.3f}", checks_in_window=n, wrong_at_end=last,
                       monitor_acted=len(read_incidents(run)))
        new = not out.exists()
        with open(out, "a", newline="") as f:
            if new:
                f.write("# accel=tcg\n# B2 ablation: monitor disarmed (ttctl disarm, observe-only). seconds_wrong = seconds of the window "
                        "the composed display showed a wrong value or no valid speed, from the per-check rows (checks.csv.gz per run).\n".replace("\\n", "\n"))
            w = csv.DictWriter(f, ABL_COLUMNS)
            if new:
                w.writeheader()
            w.writerow(row)
        if checks.exists():
            subprocess.run(["gzip", "-f", str(checks)], check=False)
        keep_small(run)
        print(f"{tag} {cls} stop={t_stop} before={before} wrong_s={row['seconds_wrong']} acted={row['monitor_acted']} load1={l1}", flush=True)


def cmd_clean(a) -> None:
    trace = read_trace()
    sha = git_sha()
    sl = Path("/tmp/tt-bench/clean.csv")
    sl.parent.mkdir(exist_ok=True)
    write_slice(sl, trace, 0.0, a.seconds)
    evroot = Path(a.evroot)
    env = {"TT_TRACE": str(sl), "TT_HOLD": "3", "TT_EVROOT": str(evroot), "TT_RUN_ID": a.tag, "TT_CONF_EXTRA": a.conf_extra}
    l0 = load1()
    sh(["scripts/scenario.sh", "none", "0", str(a.seconds + 15)], env=env, check=False)
    print(f"clean run {a.tag}: sha {sha} load1 start {l0} end {load1()}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("windows"); p.add_argument("--threshold", type=float, default=1.0); p.set_defaults(f=cmd_windows)
    p = sub.add_parser("faults")
    p.add_argument("--n", type=int, default=50, help="injections per class")
    p.add_argument("--classes", default=",".join(CLASSES))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--tag", default="b1")
    p.add_argument("--out", default="evidence/bench-faults.csv")
    p.add_argument("--evroot", default="evidence/bench-runs/b1")
    p.add_argument("--conf-extra", default="")
    p.add_argument("--settle", type=int, default=8)
    p.add_argument("--gap", type=float, default=0.0)
    p.set_defaults(f=cmd_faults)
    p = sub.add_parser("ablation")
    p.add_argument("--n", type=int, default=10, help="runs per class")
    p.add_argument("--window", type=float, default=30.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--tag", default="b2")
    p.add_argument("--out", default="evidence/bench-ablation.csv")
    p.add_argument("--evroot", default="evidence/bench-runs/b2")
    p.set_defaults(f=cmd_ablation)
    p = sub.add_parser("clean")
    p.add_argument("--seconds", type=int, default=1800)
    p.add_argument("--tag", default="b3")
    p.add_argument("--evroot", default="evidence/bench-runs/b3")
    p.add_argument("--conf-extra", default="")
    p.set_defaults(f=cmd_clean)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()

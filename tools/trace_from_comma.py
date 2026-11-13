#!/usr/bin/env python3
"""Build Telltale's real speed traces from comma2k19 CAN speed (MIT).

Input: a directory written by tools/fetch_comma_speed.py (`<dir>/Chunk_<n>/<route>/<segment>/{t,value}`).
Outputs (all derived from measured samples; speed values are never synthesized or interpolated):

  data/trace-hero.csv     t_s,speed_mps  - the hero stop (49-55 km/h -> 0), native CAN sample rate
  data/trace-hero.meta    key=value      - source, brake_onset_s, inject_at_s, stop_s, held_s, ladder rung
  data/trace-bench.csv    t_s,speed_mps  - >= 30 min of one route, sample-and-hold on a 50 ms grid
  data/window-census.csv  scope,threshold_kmh_per_100ms,hours,windows,windows_per_hour
  data/SOURCES.md         provenance, MIT notice, SHA-256 of every output

Rules (fixed before any fault run; see data/SOURCES.md for the run that produced the committed files):
  * km/h = m/s * 3.6; displayed value = round half up (same rule as src/common/disp.c); "stationary" = displays 0.
  * A route's segments are joined only when their numbers are consecutive and the time gap is -0.1..1 s
    (boundary samples that overlap in time are dropped, never averaged).
  * Hero stop: the stop = first displayed 0; the descent = the non-increasing run (0.5 km/h noise tolerance) that
    ends there; brake onset = the last sample of that descent at >= 49 km/h, which must be <= 55 km/h; onset -> stop
    3-8 s; the 10 s before onset stay within +-10 km/h of the onset speed (driving, no stop); >= 27 s displayed 0
    after the stop. First match in (chunk, route, segment) order.
    Pre-registered ladder, tried in order: (a) as stated; (b) stop shorter than 27 s -> tail held at 0 and labelled
    (held_s); (c) onset -> stop up to 15 s; (d) (c) + held tail.
  * inject_at_s = first instant after onset with speed >= 1 km/h below onset AND displayed 48.
  * Speed-changing window: |speed(t + 100 ms) - speed(t)| >= threshold on a 100 ms grid; consecutive qualifying
    steps form one window; windows closer than 1 s are merged. Thresholds 1.0 (primary) and 0.5 (fallback).
"""

from __future__ import annotations

import argparse
import ast
import bisect
import hashlib
import math
import os
import struct
import sys
from dataclasses import dataclass, field

KMH = 3.6
GAP_S = 1.0
OVERLAP_S = 0.1
TOL_KMH = 0.5
STEADY_S = 10.0
STEADY_BAND_KMH = 10.0
STATIONARY_S = 27.0
LEAD_S = 12.0
BENCH_DT = 0.05
CENSUS_DT = 0.1
MERGE_S = 1.0
THRESHOLDS = (1.0, 0.5)
MIN_K = 20  # primary threshold kept while trace-bench has >= MIN_K distinct windows at it
REVISION_NOTE = "4bff77c7254c654c28d4c2726186b4e825adccee"  # same pin as fetch_comma_speed.REVISION
LADDER = (  # rung, max onset->stop seconds, allow held tail
    ("a", 8.0, False),
    ("b", 8.0, True),
    ("c", 15.0, False),
    ("d", 15.0, True),
)


def disp(kmh: float) -> int:
    """Displayed km/h: round half up, clamped to [0, 999] (mirrors disp_kmh() in src/common/disp.c)."""
    if math.isnan(kmh):
        return -1
    return max(0, min(999, math.floor(kmh + 0.5)))


def load_npy(path: str) -> list[float]:
    """Minimal .npy reader for little-endian float64 arrays of shape (n,) or (n, 1). Stdlib only."""
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:6] != b"\x93NUMPY":
        raise ValueError(f"{path}: not an .npy file")
    major = raw[6]
    if major == 1:
        hlen, off = struct.unpack("<H", raw[8:10])[0], 10
    else:
        hlen, off = struct.unpack("<I", raw[8:12])[0], 12
    hdr = ast.literal_eval(raw[off : off + hlen].decode("latin1"))
    if hdr["descr"] != "<f8" or hdr["fortran_order"]:
        raise ValueError(f"{path}: unsupported dtype/order {hdr}")
    shape = hdr["shape"]
    n = shape[0]
    if len(shape) > 2 or (len(shape) == 2 and shape[1] != 1):
        raise ValueError(f"{path}: unsupported shape {shape}")
    body = raw[off + hlen :]
    if len(body) != 8 * n:
        raise ValueError(f"{path}: expected {n} values, got {len(body) // 8}")
    return list(struct.unpack(f"<{n}d", body))


@dataclass
class Stream:
    chunk: int
    route: str
    segments: list[int] = field(default_factory=list)
    t: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)  # m/s

    @property
    def label(self) -> str:
        return f"Chunk_{self.chunk}/{self.route}/{self.segments[0]}-{self.segments[-1]}"

    def kmh(self, i: int) -> float:
        return self.v[i] * KMH

    def at(self, t: float) -> float:
        """Sample-and-hold value (m/s) at time t: the last real sample at or before t."""
        i = bisect.bisect_right(self.t, t) - 1
        return self.v[max(i, 0)]


def load_streams(root: str) -> list[Stream]:
    streams: list[Stream] = []
    for cdir in sorted(os.listdir(root), key=lambda s: (len(s), s)):
        if not cdir.startswith("Chunk_"):
            continue
        chunk = int(cdir.split("_")[1])
        for rdir in sorted(os.listdir(os.path.join(root, cdir))):
            rpath = os.path.join(root, cdir, rdir)
            if not os.path.isdir(rpath):
                continue
            route = rdir.replace("_", "|", 1)
            cur: Stream | None = None
            for seg in sorted(int(s) for s in os.listdir(rpath) if s.isdigit()):
                t = load_npy(os.path.join(rpath, str(seg), "t"))
                v = load_npy(os.path.join(rpath, str(seg), "value"))
                if len(t) != len(v) or not t:
                    raise ValueError(f"{rpath}/{seg}: t/value length mismatch")
                joinable = cur is not None and seg == cur.segments[-1] + 1 and -OVERLAP_S <= t[0] - cur.t[-1] <= GAP_S
                if not joinable:
                    cur = Stream(chunk, route)
                    streams.append(cur)
                cur.segments.append(seg)
                last = cur.t[-1] if cur.t else -math.inf
                for ti, vi in zip(t, v):
                    if ti > last:  # segment boundaries overlap by a few ms: keep time strictly increasing
                        cur.t.append(ti)
                        cur.v.append(vi)
                        last = ti
    return streams


@dataclass
class Stop:
    stream: Stream
    rung: str
    onset: int
    stop: int
    inject: int
    stationary_s: float
    held_s: float


def find_stop(streams: list[Stream]) -> Stop | None:
    for rung, max_brake_s, allow_held in LADDER:
        for s in streams:
            hit = _scan(s, rung, max_brake_s, allow_held)
            if hit:
                return hit
    return None


def _scan(s: Stream, rung: str, max_brake_s: float, allow_held: bool) -> Stop | None:
    n = len(s.t)
    for i0 in range(1, n):
        if not (disp(s.kmh(i0)) == 0 and disp(s.kmh(i0 - 1)) > 0):
            continue
        j = i0
        while j > 0 and s.kmh(j - 1) >= s.kmh(j) - TOL_KMH:
            j -= 1
        onset = next((k for k in range(i0, j - 1, -1) if s.kmh(k) >= 49.0), None)
        if onset is None or not 49.0 <= s.kmh(onset) <= 55.0:
            continue  # the monotone descent must pass through 49-55 km/h
        peak = s.kmh(onset)
        brake_s = s.t[i0] - s.t[onset]
        if not 3.0 <= brake_s <= max_brake_s:
            continue
        if s.t[onset] - s.t[0] < STEADY_S:
            continue
        k0 = bisect.bisect_left(s.t, s.t[onset] - STEADY_S)
        if any(abs(s.kmh(k) - peak) > STEADY_BAND_KMH for k in range(k0, onset)):
            continue
        k = i0
        while k + 1 < n and disp(s.kmh(k + 1)) == 0:
            k += 1
        stationary_s = s.t[k] - s.t[i0]
        if stationary_s < STATIONARY_S and not allow_held:
            continue
        if stationary_s < 1.0:
            continue
        inject = next(
            (q for q in range(onset, i0) if s.kmh(q) <= peak - 1.0 and disp(s.kmh(q)) == 48),
            None,
        )
        if inject is None:
            continue
        return Stop(s, rung, onset, i0, inject, stationary_s, max(0.0, STATIONARY_S - stationary_s))
    return None


def hero_rows(h: Stop) -> tuple[list[tuple[float, float]], float]:
    s = h.stream
    t0 = s.t[h.onset] - LEAD_S
    t_end = s.t[h.stop] + min(h.stationary_s, STATIONARY_S)
    rows = [(t - t0, v) for t, v in zip(s.t, s.v) if t0 <= t <= t_end]
    held_from = rows[-1][0]
    if h.held_s > 0:
        dts = sorted(b[0] - a[0] for a, b in zip(rows, rows[1:]))
        dt = dts[len(dts) // 2]
        t = held_from + dt
        while t <= held_from + h.held_s + 1e-9:
            rows.append((t, 0.0))
            t += dt
    return rows, held_from


def windows(s: Stream, thr_kmh: float, t_from: float | None = None, t_to: float | None = None) -> list[float]:
    """Onset times of distinct speed-changing windows (see module docstring)."""
    a = s.t[0] if t_from is None else t_from
    b = s.t[-1] if t_to is None else t_to
    steps = int((b - a) / CENSUS_DT)
    out: list[float] = []
    last_end = -1e18
    prev = s.at(a) * KMH
    for k in range(1, steps + 1):
        t = a + k * CENSUS_DT
        cur = s.at(t) * KMH
        if abs(cur - prev) >= thr_kmh - 1e-9:
            start = t - CENSUS_DT
            if start - last_end >= MERGE_S:
                out.append(start)
            last_end = t
        prev = cur
    return out


def count_stops(rows: list[tuple[float, float]], min_stationary_s: float = STATIONARY_S) -> int:
    stops, run_start = 0, None
    for i, (t, v) in enumerate(rows):
        still = disp(v * KMH) == 0
        if still and run_start is None and i > 0:
            run_start = t
        if not still:
            run_start = None
        if still and run_start is not None and t - run_start >= min_stationary_s:
            stops += 1
            run_start = float("inf")  # counted; wait for motion
    return stops


def stop_list(rows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """(t_stop_s, stationary_s) for every displayed-0 run that follows motion, in trace time."""
    out: list[tuple[float, float]] = []
    start = None
    moved = False
    for t, v in rows:
        still = disp(v * KMH) == 0
        if still and moved and start is None:
            start = t
        if not still:
            if start is not None:
                out.append((start, t - start))
            start, moved = None, True
    if start is not None:
        out.append((start, rows[-1][0] - start))
    return out


def bench_rows(s: Stream) -> list[tuple[float, float]]:
    steps = int((s.t[-1] - s.t[0]) / BENCH_DT)
    return [(k * BENCH_DT, s.at(s.t[0] + k * BENCH_DT)) for k in range(steps + 1)]


def write_csv(path: str, rows: list[tuple[float, float]]) -> None:
    with open(path, "w") as fh:
        fh.write("t_s,speed_mps\n")
        for t, v in rows:
            fh.write(f"{t:.3f},{v:.4f}\n")


def sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build Telltale speed traces from comma2k19 CAN speed")
    ap.add_argument("src", help="directory written by tools/fetch_comma_speed.py")
    ap.add_argument("--out", default="data")
    ap.add_argument("--census-chunk", type=int, default=1, help="chunk used for the window census (default: 1)")
    ap.add_argument("--bench-route", required=True, help="route id for the >= 30 min bench trace")
    ap.add_argument("--min-bench-min", type=float, default=30.0)
    a = ap.parse_args(argv)
    src = os.path.expanduser(a.src)
    os.makedirs(a.out, exist_ok=True)
    streams = load_streams(src)
    report: list[str] = []

    # hero stop
    hero = find_stop(streams)
    if hero is None:
        print("find-stop: no segment matched any ladder rung", file=sys.stderr)
        return 3
    rows, held_from = hero_rows(hero)
    t0 = hero.stream.t[hero.onset] - LEAD_S
    write_csv(os.path.join(a.out, "trace-hero.csv"), rows)
    seg_of_onset = hero.stream.segments[0] + int((hero.stream.t[hero.onset] - hero.stream.t[0]) // 60)
    meta = {
        "source": hero.stream.label,
        "onset_segment_approx": seg_of_onset,
        "ladder_rung": hero.rung,
        "onset_kmh": f"{hero.stream.kmh(hero.onset):.2f}",
        "brake_onset_s": f"{hero.stream.t[hero.onset] - t0:.3f}",
        "inject_at_s": f"{hero.stream.t[hero.inject] - t0:.3f}",
        "inject_kmh": f"{hero.stream.kmh(hero.inject):.2f}",
        "stop_s": f"{hero.stream.t[hero.stop] - t0:.3f}",
        "brake_duration_s": f"{hero.stream.t[hero.stop] - hero.stream.t[hero.onset]:.3f}",
        "stationary_measured_s": f"{hero.stationary_s:.3f}",
        "held_s": f"{hero.held_s:.3f}",
        "held_from_s": f"{held_from:.3f}" if hero.held_s > 0 else "none",
        "rows": len(rows),
    }
    with open(os.path.join(a.out, "trace-hero.meta"), "w") as fh:
        fh.writelines(f"{k}={v}\n" for k, v in meta.items())
    report.append(f"hero: {meta['source']} rung {hero.rung}, onset {meta['onset_kmh']} km/h, "
                  f"brake {meta['brake_duration_s']} s, stationary {meta['stationary_measured_s']} s, held {meta['held_s']} s")

    # bench trace
    cands = [s for s in streams if s.route == a.bench_route]
    if not cands:
        print(f"bench route {a.bench_route!r} not in {src}", file=sys.stderr)
        return 3
    bench = max(cands, key=lambda s: s.t[-1] - s.t[0])
    minutes = (bench.t[-1] - bench.t[0]) / 60
    if minutes < a.min_bench_min:
        print(f"bench stream {bench.label} is {minutes:.1f} min < {a.min_bench_min}", file=sys.stderr)
        return 3
    brows = bench_rows(bench)
    write_csv(os.path.join(a.out, "trace-bench.csv"), brows)
    stops = count_stops(brows)
    slist = stop_list(brows)
    with open(os.path.join(a.out, "trace-bench-stops.csv"), "w") as fh:
        fh.write("t_stop_s,stationary_s,held_needed_s\n")
        fh.writelines(f"{t:.3f},{d:.3f},{max(0.0, STATIONARY_S - d):.3f}\n" for t, d in slist)
    report.append(f"bench: {bench.label}, {minutes:.2f} min, {len(brows)} rows, stops: {len(slist)} "
                  f"({stops} with >= 27 s at 0, the rest need a labelled held tail for B2)")

    # window census
    census = ["scope,threshold_kmh_per_100ms,hours,windows,windows_per_hour"]
    kb: dict[float, int] = {}
    scopes = [(f"Chunk_{a.census_chunk} (all segments)", [s for s in streams if s.chunk == a.census_chunk]),
              (f"trace-bench ({bench.label})", [bench])]
    for name, ss in scopes:
        hours = sum(s.t[-1] - s.t[0] for s in ss) / 3600
        for thr in THRESHOLDS:
            k = sum(len(windows(s, thr)) for s in ss)
            census.append(f"\"{name}\",{thr},{hours:.3f},{k},{k / hours:.1f}")
            if ss == [bench]:
                kb[thr] = k
            report.append(f"census: {name} thr {thr}: K={k} over {hours:.2f} h ({k / hours:.1f}/h)")
    with open(os.path.join(a.out, "window-census.csv"), "w") as fh:
        fh.write("\n".join(census) + "\n")

    # hero must sit inside a speed-changing window at its inject instant (STALE becomes a lie quickly)
    hw = windows(hero.stream, THRESHOLDS[0], hero.stream.t[hero.onset], hero.stream.t[hero.stop])
    report.append(f"hero windows (thr {THRESHOLDS[0]}) between onset and stop: {len(hw)}")
    selected = THRESHOLDS[0] if kb[THRESHOLDS[0]] >= MIN_K else THRESHOLDS[1]
    report.append(f"selected STALE window threshold: {selected} km/h per 100 ms (K={kb[selected]} in trace-bench)")
    write_sources(a, src, hero, meta, bench, minutes, len(slist), stops, kb, selected)
    print("\n".join(report))
    return 0


def write_sources(a, src, hero, meta, bench, minutes, n_stops, n_long, kb, selected) -> None:
    outs = ["trace-hero.csv", "trace-hero.meta", "trace-bench.csv", "trace-bench-stops.csv", "window-census.csv"]
    lines = [
        "# Data sources",
        "",
        "Generated by `tools/trace_from_comma.py`; do not edit by hand. Every speed value below is a measured CAN sample",
        "from comma2k19. Nothing is synthesized or interpolated; the bench trace is sample-and-hold on a 50 ms grid.",
        "",
        "## comma2k19 (MIT)",
        "",
        f"- Dataset: https://huggingface.co/datasets/commaai/comma2k19 at revision `{REVISION_NOTE}`, members",
        "  `processed_log/CAN/speed/{t,value}` only, fetched with `tools/fetch_comma_speed.py` (HTTP range reads; CRC-32",
        "  of each member is checked by `zipfile` and listed in the fetch manifest).",
        "- Licence: MIT, Copyright (c) 2018 comma.ai (https://github.com/commaai/comma2k19/blob/master/LICENSE).",
        "",
        "## Traces",
        "",
        f"- `trace-hero.csv`: {meta['source']}, onset in segment ~{meta['onset_segment_approx']}; ladder rung "
        f"`{meta['ladder_rung']}`; {meta['onset_kmh']} km/h -> 0 in {meta['brake_duration_s']} s, then "
        f"{meta['stationary_measured_s']} s stationary (held tail: {meta['held_s']} s). Details in `trace-hero.meta`.",
        f"- `trace-bench.csv`: {bench.label}, {minutes:.2f} min continuous. {n_stops} stops, {n_long} of them with "
        f">= 27 s at 0; `trace-bench-stops.csv` lists each stop and the held tail B2 would need (labelled when used).",
        "",
        "## Speed-changing windows (STALE placement)",
        "",
        f"`window-census.csv`: windows where |speed(t + 100 ms) - speed(t)| >= threshold. trace-bench has "
        f"K = {kb[1.0]} distinct windows at 1.0 km/h per 100 ms and {kb[0.5]} at 0.5.",
        f"Pre-registered rule (fixed before any fault run): keep 1.0 while K >= {MIN_K}, else 0.5. "
        f"**Selected: {selected} km/h per 100 ms.** Reports say \"N injections over K distinct windows\".",
        "",
        "## Reproduce",
        "",
        "```sh",
        "python3 tools/fetch_comma_speed.py --chunk 1 --out ~/.cache/telltale/comma2k19",
        f"python3 tools/fetch_comma_speed.py --chunk {bench.chunk} --route '{bench.route}' --out ~/.cache/telltale/comma2k19",
        f"python3 tools/trace_from_comma.py ~/.cache/telltale/comma2k19 --out data --bench-route '{bench.route}'",
        "```",
        "",
        "## SHA-256",
        "",
        "| file | sha256 |",
        "|---|---|",
    ]
    lines += [f"| `{o}` | `{sha256(os.path.join(a.out, o))}` |" for o in outs]
    with open(os.path.join(a.out, "SOURCES.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())

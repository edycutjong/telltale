#!/usr/bin/env python3
"""Fetch only the CAN speed arrays from a comma2k19 chunk, without downloading the chunk.

comma2k19 (MIT, https://github.com/commaai/comma2k19) ships as ten ~9 GB zip chunks. Telltale only needs
`processed_log/CAN/speed/{t,value}` per one-minute segment (a few tens of KB each), so this tool reads the zip's
central directory with HTTP range requests and extracts just those members. Output goes OUTSIDE the repo:

    <out>/Chunk_<n>/<route>/<segment>/{t,value}     (numpy .npy files, as stored in the dataset)
    <out>/Chunk_<n>/manifest.csv                     chunk,route,segment,member,crc32,size

Usage:
    python3 tools/fetch_comma_speed.py --chunk 1 --out ~/.cache/telltale/comma2k19
    python3 tools/fetch_comma_speed.py --chunk 8 --route '99c94dc769b5d96e|2018-09-19--16-09-16' --out ...
    for n in 1 2 3 4 5 6 7 8 9 10; do python3 tools/fetch_comma_speed.py --chunk $n --survey data/route-survey.csv; done

Stdlib only. The dataset revision is pinned so the same bytes come back on every run.
"""

from __future__ import annotations

import argparse
import http.client
import io
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

REPO = "https://huggingface.co/datasets/commaai/comma2k19/resolve"
REVISION = "4bff77c7254c654c28d4c2726186b4e825adccee"  # HF commit pinned 2026-11-14
UA = "telltale-fetch/1 (+https://github.com/edycutjong/telltale)"
RETRIES = 4  # attempts per range request; transient failures only (network errors, HTTP 429/5xx)
BACKOFF_S = 2.0  # doubled after each failed attempt
SPEED_RE = re.compile(r"^Chunk_(\d+)/([^/]+)/(\d+)/processed_log/CAN/speed/(t|value)$")


class ShortReadError(OSError):
    """The server sent fewer bytes than the range asked for (a dropped connection); retried."""


class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file via HTTP Range requests."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.pos = 0
        self.requests = 0
        self.retries = 0
        self.size = self._total_size()

    def _get(self, start: int, end: int) -> tuple[bytes, str]:
        """One range request, retried with backoff on transient failures; a 200 (Range ignored) is fatal."""
        delay = BACKOFF_S
        for attempt in range(1, RETRIES + 1):
            try:
                return self._get_once(start, end)
            except urllib.error.HTTPError as e:
                if (e.code != 429 and e.code < 500) or attempt == RETRIES:
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.IncompleteRead, ShortReadError):
                if attempt == RETRIES:
                    raise
            self.retries += 1
            time.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    def _get_once(self, start: int, end: int) -> tuple[bytes, str]:
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            self.requests += 1
            if r.status != 206:
                raise OSError(f"server ignored the Range header (HTTP {r.status})")
            data = r.read()
            if len(data) != end - start + 1:
                raise ShortReadError(f"short range read: asked {end - start + 1} bytes, got {len(data)}")
            return data, r.headers.get("Content-Range", "")

    def _total_size(self) -> int:
        _, cr = self._get(0, 0)
        m = re.match(r"bytes 0-0/(\d+)$", cr)
        if not m:
            raise OSError(f"unexpected Content-Range: {cr!r}")
        return int(m.group(1))

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self.pos, 2: self.size}[whence]
        self.pos = base + offset
        return self.pos

    def readinto(self, buf) -> int:  # type: ignore[override]
        n = len(buf)
        if n == 0 or self.pos >= self.size:
            return 0
        end = min(self.size, self.pos + n) - 1
        data, _ = self._get(self.pos, end)
        buf[: len(data)] = data
        self.pos += len(data)
        return len(data)


def safe_name(route: str) -> str:
    """Route ids contain '|'; keep them readable but filesystem-safe."""
    return route.replace("|", "_")


def longest_run(segs: list[int]) -> tuple[int, int]:
    """Longest run of consecutive segment numbers -> (length, first segment). Empty -> (0, -1)."""
    best, start = 0, -1
    run, run_start, prev = 0, -1, None
    for x in sorted(set(segs)):
        if prev is not None and x == prev + 1:
            run += 1
        else:
            run, run_start = 1, x
        if run > best:
            best, start = run, run_start
        prev = x
    return best, start


def survey(zf: zipfile.ZipFile, chunk: int) -> list[str]:
    """CSV rows (no header) for every route in the chunk's zip directory that has CAN speed members.

    Reads only the central directory; no member is downloaded. A segment counts when both speed/t and
    speed/value are listed for it.
    """
    have: dict[tuple[str, int], set[str]] = {}
    for info in zf.infolist():
        m = SPEED_RE.match(info.filename)
        if m:
            have.setdefault((m.group(2), int(m.group(3))), set()).add(m.group(4))
    routes: dict[str, list[int]] = {}
    for (route, seg), kinds in have.items():
        if kinds == {"t", "value"}:
            routes.setdefault(route, []).append(seg)
    rows = []
    for route in sorted(routes):
        n, first = longest_run(routes[route])
        rows.append(f"{chunk},{route},{len(routes[route])},{n},{first}")
    return rows


SURVEY_HEADER = "chunk,route,segments_with_speed,longest_consecutive_run,run_first_segment"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chunk", type=int, required=True, choices=range(1, 11))
    ap.add_argument("--survey", metavar="CSV", help="append this chunk's per-route segment survey to CSV "
                    "(zip directory only, nothing extracted) and exit")
    ap.add_argument("--route", help="only this route id (e.g. 'dongle|YYYY-MM-DD--hh-mm-ss')")
    ap.add_argument("--out", help="output directory (keep it outside the repo); required unless --survey")
    ap.add_argument("--revision", default=REVISION)
    a = ap.parse_args(argv)

    url = f"{REPO}/{a.revision}/raw_data/Chunk_{a.chunk}.zip"
    rf = RangeFile(url)
    zf = zipfile.ZipFile(io.BufferedReader(rf, buffer_size=1 << 16))
    if a.survey:
        rows = survey(zf, a.chunk)
        new_file = not os.path.exists(a.survey)
        with open(a.survey, "a") as fh:
            if new_file:
                fh.write(SURVEY_HEADER + "\n")
            fh.writelines(r + "\n" for r in rows)
        print(f"Chunk_{a.chunk}: {len(rows)} routes surveyed, {rf.requests} range requests -> {a.survey}")
        return 0
    if not a.out:
        ap.error("--out is required unless --survey is given")
    members = []
    for info in zf.infolist():
        m = SPEED_RE.match(info.filename)
        if m and (a.route is None or m.group(2) == a.route):
            members.append((m.group(2), int(m.group(3)), m.group(4), info))
    if not members:
        print(f"no speed members matched in Chunk_{a.chunk}", file=sys.stderr)
        return 2
    members.sort(key=lambda x: (x[0], x[1], x[2]))
    root = os.path.join(os.path.expanduser(a.out), f"Chunk_{a.chunk}")
    rows = ["chunk,route,segment,member,crc32,size"]
    for route, seg, kind, info in members:
        d = os.path.join(root, safe_name(route), str(seg))
        os.makedirs(d, exist_ok=True)
        with zf.open(info) as src, open(os.path.join(d, kind), "wb") as dst:  # zipfile checks the CRC on read
            dst.write(src.read())
        rows.append(f"{a.chunk},{route},{seg},{kind},{info.CRC:08x},{info.file_size}")
    with open(os.path.join(root, "manifest.csv"), "w") as fh:
        fh.write("\n".join(rows) + "\n")
    segs = len({(r, s) for r, s, _, _ in members})
    print(f"Chunk_{a.chunk}: {segs} segments, {len(members)} members, {rf.requests} range requests ({rf.retries} retried) -> {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

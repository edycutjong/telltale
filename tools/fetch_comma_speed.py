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

Stdlib only. The dataset revision is pinned so the same bytes come back on every run.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import urllib.request
import zipfile

REPO = "https://huggingface.co/datasets/commaai/comma2k19/resolve"
REVISION = "4bff77c7254c654c28d4c2726186b4e825adccee"  # HF commit pinned 2026-11-14
UA = "telltale-fetch/1 (+https://github.com/edycutjong/telltale)"
SPEED_RE = re.compile(r"^Chunk_(\d+)/([^/]+)/(\d+)/processed_log/CAN/speed/(t|value)$")


class RangeFile(io.RawIOBase):
    """Read-only, seekable view of a remote file via HTTP Range requests."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.pos = 0
        self.requests = 0
        self.size = self._total_size()

    def _get(self, start: int, end: int) -> tuple[bytes, str]:
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            self.requests += 1
            if r.status != 206:
                raise OSError(f"server ignored the Range header (HTTP {r.status})")
            return r.read(), r.headers.get("Content-Range", "")

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chunk", type=int, required=True, choices=range(1, 11))
    ap.add_argument("--route", help="only this route id (e.g. 'dongle|YYYY-MM-DD--hh-mm-ss')")
    ap.add_argument("--out", required=True, help="output directory (keep it outside the repo)")
    ap.add_argument("--revision", default=REVISION)
    a = ap.parse_args(argv)

    url = f"{REPO}/{a.revision}/raw_data/Chunk_{a.chunk}.zip"
    rf = RangeFile(url)
    zf = zipfile.ZipFile(io.BufferedReader(rf, buffer_size=1 << 16))
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
    print(f"Chunk_{a.chunk}: {segs} segments, {len(members)} members, {rf.requests} range requests -> {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

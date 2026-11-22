#!/usr/bin/env python3
"""Decode a ttrec recording (tile-delta frames read back from QNX Screen on the target) into PNG frames.

    tools/ttv_decode.py <in.ttv> <out_dir>

Writes out_dir/f00000.png ... (one PNG per recorded frame, RGB) and out_dir/index.json:
{"w", "h", "tile", "frames": [[t_ns, "f00000.png", n_tiles], ...]} -- t_ns is the target's CLOCK_MONOTONIC,
the clock the Telltale logs use, so frames line up with log events. Format: see src/tools/ttrec.c.
"""
import json
import struct
import sys
from pathlib import Path


def read_frames(data):
    """Yield (t_ns, tiles) per frame; tiles = [(tx, ty, bgra_bytes)]. Header -> (w, h, tile) via first item."""
    if data[:4] != b"TTV1":
        raise ValueError("not a TTV1 file")
    w, h, tile = struct.unpack_from("<III", data, 4)
    yield (w, h, tile)
    off, tsz = 16, tile * tile * 4
    while off + 12 <= len(data):
        t_ns, n = struct.unpack_from("<QI", data, off)
        off += 12
        if off + n * (4 + tsz) > len(data):
            print(f"ttv_decode: dropped a truncated last frame at byte {off - 12}", file=sys.stderr)
            break  # truncated last frame (recorder killed mid-write): drop it, say so
        tiles = []
        for _ in range(n):
            tx, ty = struct.unpack_from("<HH", data, off)
            tiles.append((tx, ty, data[off + 4:off + 4 + tsz]))
            off += 4 + tsz
        yield (t_ns, tiles)


def decode(path, out_dir, write_png=True):
    import numpy as np
    from PIL import Image

    gen = read_frames(Path(path).read_bytes())
    w, h, tile = next(gen)
    canvas = np.zeros((h, w, 4), dtype=np.uint8)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for i, (t_ns, tiles) in enumerate(gen):
        for tx, ty, raw in tiles:
            canvas[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile] = np.frombuffer(raw, np.uint8).reshape(tile, tile, 4)
        name = f"f{i:05d}.png"
        if write_png:
            Image.fromarray(canvas[:, :, 2::-1].copy(), "RGB").save(out / name, compress_level=1)
        index.append([t_ns, name, len(tiles)])
    (out / "index.json").write_text(json.dumps({"w": w, "h": h, "tile": tile, "frames": index}))
    return w, h, tile, index, canvas


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 1
    w, h, tile, index, _ = decode(argv[1], argv[2])
    if not index:
        print(f"ttv_decode: {argv[1]} holds no complete frame", file=sys.stderr)
        return 1
    span = (index[-1][0] - index[0][0]) / 1e9
    print(f"ttv_decode: {len(index)} frames {w}x{h} over {span:.2f} s -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

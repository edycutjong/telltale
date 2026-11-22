"""Round trip for the ttrec tile-delta format (src/tools/ttrec.c) and its host decoder (tools/ttv_decode.py)."""
import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import ttv_decode  # noqa: E402

try:
    import numpy  # noqa: F401
    HAVE_NP = True
except ImportError:
    HAVE_NP = False
try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False
DECODE_DEPS = HAVE_NP and HAVE_PIL   # decode() imports both

TILE = 32


def tile_bytes(b, g, r):
    return bytes([b, g, r, 255]) * (TILE * TILE)


def encode(w, h, frames):
    """frames = [(t_ns, [(tx, ty, (b, g, r)), ...])] -> TTV1 bytes, written the way ttrec writes them."""
    out = bytearray(b"TTV1" + struct.pack("<III", w, h, TILE))
    for t_ns, tiles in frames:
        out += struct.pack("<QI", t_ns, len(tiles))
        for tx, ty, bgr in tiles:
            out += struct.pack("<HH", tx, ty) + tile_bytes(*bgr)
    return bytes(out)


class TtvTest(unittest.TestCase):
    def test_reader_parses_header_and_deltas(self):
        data = encode(64, 32, [(10, [(0, 0, (0, 0, 0)), (1, 0, (0, 0, 0))]), (20, [(1, 0, (1, 2, 3))])])
        items = list(ttv_decode.read_frames(data))
        self.assertEqual(items[0], (64, 32, TILE))
        self.assertEqual([t for t, _ in items[1:]], [10, 20])
        self.assertEqual(len(items[2][1]), 1)

    def test_truncated_last_frame_is_dropped(self):
        data = encode(32, 32, [(1, [(0, 0, (0, 0, 0))]), (2, [(0, 0, (9, 9, 9))])])
        items = list(ttv_decode.read_frames(data[:-5]))
        self.assertEqual(len(items), 2)  # header + the one complete frame

    @unittest.skipUnless(DECODE_DEPS, "numpy and Pillow needed (decode() imports both)")
    def test_header_only_recording_is_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "empty.ttv"
            p.write_bytes(encode(32, 32, []))
            self.assertEqual(ttv_decode.main(["ttv_decode.py", str(p), str(Path(d) / "out")]), 1)

    def test_partial_header_tail_is_reported(self):
        data = encode(32, 32, [(1, [])]) + bytes(5)   # one complete empty frame, then 5 bytes of a cut frame header
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            items = list(ttv_decode.read_frames(data))
        self.assertEqual(len(items), 2)   # header + the one complete frame
        self.assertIn("5 trailing bytes", err.getvalue())

    def test_bad_magic(self):
        with self.assertRaises(ValueError):
            next(ttv_decode.read_frames(b"NOPE" + bytes(12)))

    @unittest.skipUnless(DECODE_DEPS, "numpy and Pillow needed (decode() imports both)")
    def test_decode_applies_tiles_bgra(self):
        data = encode(64, 32, [(5, [(0, 0, (0, 0, 0)), (1, 0, (0, 0, 0))]), (9, [(1, 0, (10, 20, 30))])])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.ttv"
            p.write_bytes(data)
            w, h, tile, index, canvas = ttv_decode.decode(p, Path(d) / "out", write_png=False)
        self.assertEqual((w, h, tile), (64, 32, TILE))
        self.assertEqual([i[0] for i in index], [5, 9])
        self.assertEqual(tuple(canvas[0, 40, :3]), (10, 20, 30))  # BGRA stored: b=10 g=20 r=30
        self.assertEqual(tuple(canvas[0, 40, 2::-1]), (30, 20, 10))  # the PNG slice: RGB
        self.assertEqual(tuple(canvas[0, 0, :3]), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()

"""Unit tests for tools/trace_from_comma.py over SYNTHETIC speed arrays (test fixtures, not measurements)."""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import trace_from_comma as T  # noqa: E402

DT = 0.012  # ~83 Hz, like comma2k19 CAN speed


def write_npy(path, values, column=False):
    shape = f"({len(values)}, 1)" if column else f"({len(values)},)"
    hdr = "{'descr': '<f8', 'fortran_order': False, 'shape': %s, }" % shape
    hdr = hdr + " " * (63 - (10 + len(hdr)) % 64) + "\n"
    with open(path, "wb") as fh:
        fh.write(b"\x93NUMPY\x01\x00" + struct.pack("<H", len(hdr)) + hdr.encode("latin1"))
        fh.write(struct.pack(f"<{len(values)}d", *values))


def profile(points):
    """Piecewise-linear km/h profile [(t_s, kmh), ...] sampled every DT -> (t, m/s) lists."""
    t, v = [], []
    k = 0
    while True:
        x = k * DT
        if x > points[-1][0]:
            break
        for (t0, a), (t1, b) in zip(points, points[1:]):
            if t0 <= x <= t1:
                v.append((a + (b - a) * (x - t0) / (t1 - t0)) / T.KMH)
                break
        t.append(1000.0 + x)
        k += 1
    return t, v


def make_dataset(root, segments):
    """segments: {(chunk, route, seg): (t, v)} -> fetch_comma_speed.py layout."""
    for (chunk, route, seg), (t, v) in segments.items():
        d = os.path.join(root, f"Chunk_{chunk}", route.replace("|", "_"), str(seg))
        os.makedirs(d)
        write_npy(os.path.join(d, "t"), t)
        write_npy(os.path.join(d, "value"), v, column=True)


class TestTrace(unittest.TestCase):
    def test_disp_matches_c_rule(self):
        self.assertEqual(T.disp(0.49), 0)
        self.assertEqual(T.disp(0.5), 1)
        self.assertEqual(T.disp(48.49), 48)
        self.assertEqual(T.disp(48.5), 49)
        self.assertEqual(T.disp(5000), 999)
        self.assertEqual(T.disp(float("nan")), -1)

    def test_npy_roundtrip_both_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            for col in (False, True):
                p = os.path.join(d, f"x{col}")
                write_npy(p, [1.5, -2.25, 3.0], column=col)
                self.assertEqual(T.load_npy(p), [1.5, -2.25, 3.0])

    def test_npy_rejects_wrong_dtype(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad")
            hdr = "{'descr': '<f4', 'fortran_order': False, 'shape': (1,), }".ljust(117) + "\n"
            with open(p, "wb") as fh:
                fh.write(b"\x93NUMPY\x01\x00" + struct.pack("<H", len(hdr)) + hdr.encode() + b"\0\0\0\0")
            with self.assertRaises(ValueError):
                T.load_npy(p)

    def test_overlapping_segments_join_strictly_increasing(self):
        t1, v1 = profile([(0, 50), (10, 50)])
        t2 = [t1[-1] - 0.005 + i * DT for i in range(100)]
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t1, v1), (1, "r|a", 2): (t2, [50 / T.KMH] * 100)})
            ss = T.load_streams(d)
            self.assertEqual(len(ss), 1)
            self.assertEqual(ss[0].segments, [1, 2])
            self.assertTrue(all(b > a for a, b in zip(ss[0].t, ss[0].t[1:])))

    def test_gap_splits_stream(self):
        t1, v1 = profile([(0, 50), (10, 50)])
        t2 = [t1[-1] + 5.0 + i * DT for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t1, v1), (1, "r|a", 2): (t2, [0.0] * 10)})
            self.assertEqual(len(T.load_streams(d)), 2)

    def test_find_stop_rung_a_and_inject_at_48(self):
        # cruise at 52, coast to 49.6 by t=20, brake to 0 by t=25 (5 s), stand still 30 s
        t, v = profile([(0, 52), (18, 52), (20, 49.6), (25, 0), (55, 0)])
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t, v)})
            hit = T.find_stop(T.load_streams(d))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.rung, "a")
        self.assertEqual(hit.held_s, 0.0)
        s = hit.stream
        self.assertTrue(49.0 <= s.kmh(hit.onset) <= 55.0)
        self.assertEqual(T.disp(s.kmh(hit.inject)), 48)
        self.assertLessEqual(s.kmh(hit.inject), s.kmh(hit.onset) - 1.0)
        self.assertTrue(3.0 <= s.t[hit.stop] - s.t[hit.onset] <= 8.0)

    def test_short_standstill_uses_held_rung_and_labels_it(self):
        t, v = profile([(0, 50), (15, 50), (20, 0), (30, 0), (31, 10)])
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t, v)})
            hit = T.find_stop(T.load_streams(d))
        self.assertEqual(hit.rung, "b")
        self.assertGreater(hit.held_s, 16.0)
        rows, held_from = T.hero_rows(hit)
        self.assertTrue(all(sp == 0.0 for tt, sp in rows if tt > held_from))

    def test_slow_brake_needs_rung_c(self):
        t, v = profile([(0, 50), (15, 50), (27, 0), (60, 0)])  # 49 -> 0 in ~11.8 s
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t, v)})
            self.assertEqual(T.find_stop(T.load_streams(d)).rung, "c")

    def test_non_monotone_descent_is_rejected(self):
        # 52 -> 49.5 -> 20, back up to 45, then 0: the 45 -> 0 part never passed 49, the 49.5 part is not monotone to 0
        t, v = profile([(0, 52), (15, 52), (16, 49.5), (17, 20), (18, 45), (21, 0), (60, 0)])
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t, v)})
            self.assertIsNone(T.find_stop(T.load_streams(d)))

    def test_opening_standstill_is_not_a_stop(self):
        rows = [(i * 0.05, sp / T.KMH) for i, sp in enumerate([0] * 800 + [30] * 20 + [0] * 600)]
        self.assertEqual(len(T.stop_list(rows)), 1)
        self.assertEqual(T.count_stops(rows), 1)

    def test_segment_provenance_per_sample(self):
        t1, v1 = profile([(0, 50), (10, 50)])
        t2 = [t1[-1] + DT + i * DT for i in range(10)]
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 4): (t1, v1), (1, "r|a", 5): (t2, [0.0] * 10)})
            s = T.load_streams(d)[0]
        self.assertEqual((s.seg[0], s.seg[-1], len(s.seg)), (4, 5, len(s.t)))

    def test_no_stop_returns_none(self):
        t, v = profile([(0, 50), (60, 60)])
        with tempfile.TemporaryDirectory() as d:
            make_dataset(d, {(1, "r|a", 1): (t, v)})
            self.assertIsNone(T.find_stop(T.load_streams(d)))

    def test_windows_counts_and_merges(self):
        # two brakings at 20 km/h per s (2 km/h per 100 ms), 20 s apart
        t, v = profile([(0, 60), (5, 60), (6, 40), (26, 40), (27, 20), (40, 20)])
        s = T.Stream(1, "r|a", segments=[1], t=t, v=v)
        self.assertEqual(len(T.windows(s, 1.0)), 2)
        self.assertEqual(len(T.windows(s, 3.0)), 0)

    def test_stop_list_and_count(self):
        rows = [(i * 0.05, sp / T.KMH) for i, sp in enumerate([30] * 20 + [0] * 700 + [20] * 20 + [0] * 100)]
        st = T.stop_list(rows)
        self.assertEqual(len(st), 2)
        self.assertAlmostEqual(st[0][1], 35.0, places=6)
        self.assertEqual(T.count_stops(rows), 1)


if __name__ == "__main__":
    unittest.main()

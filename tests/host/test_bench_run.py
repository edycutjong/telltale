"""Unit tests for scripts/bench_run.py (row extraction, windows, ablation scoring) over hand-written run directories.
The directories built here are test inputs, not measurements."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import bench  # noqa: E402
import bench_run as br  # noqa: E402

INC = "TT-FRZ-0001,TICK_STALL,hung,45,45,{last},{det},19.9,{safe},{kill},{reg},{true},1,0.0,8749089,8990742,0,0\n"
META = {"sha": "abc1234", "seed": 42, "period_ms": "20", "tick_stall_ms": "150", "onset": 12.0, "load1": "3.0"}


def run_dir(tmp: str, cls: str, t_inj: int, incidents: str, hog: bool = False) -> Path:
    d = Path(tmp) / "r"
    d.mkdir()
    if hog:
        (d / "hog.log").write_text(f"t_hog_start_ns={t_inj} prio=40 ms=3000 cpu=3\n")
    else:
        (d / "inject.txt").write_text(f"trace_s=3.200 speed_kmh=45.00\nt_command_ns={t_inj - 5000} class=x arg=0 run=1\n")
        (d / "cluster-bench.csv").write_text(f"1,2,{cls},{t_inj},-1,-1\n")
    (d / "incidents.csv").write_text(incidents)
    return d


class Rows(unittest.TestCase):
    def test_detected_row_validates_in_bench(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = run_dir(tmp, "FREEZE", 1_000_000_000, INC.format(last=900_000_000, det=1_080_000_000, safe=1_100_000_000,
                                                                   kill=1_150_000_000, reg=1_400_000_000, true=1_500_000_000))
            row = br.make_row(d, "b1-001", "FREEZE", dict(META))
            self.assertEqual(row["missed"], "0")
            self.assertEqual(row["t_command_ns"], 1_000_000_000 - 5000)
            out = Path(tmp) / "f.csv"
            br.append_row(out, row)
            res = bench.analyze_faults(bench.read_table(out.read_text(), bench.FAULT_COLUMNS).rows)
            c = res["classes"]["FREEZE"]
            self.assertEqual((c["total"], c["misses"], c["failed"], c["inconsistent"]), (1, 0, 0, 0))
            self.assertAlmostEqual(c["detect_ms"]["p50"], 80.0)

    def test_no_incident_is_a_miss_not_a_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = run_dir(tmp, "BLANK", 5_000_000_000, "")
            row = br.make_row(d, "b1-002", "BLANK", dict(META))
            self.assertEqual(row["missed"], "1")
            self.assertEqual(row["t_detect_ns"], "")

    def test_detection_later_than_2s_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = run_dir(tmp, "STALE", 1_000_000_000, INC.format(last=0, det=3_500_000_000, safe=0, kill=0, reg=0, true=0))
            self.assertEqual(br.make_row(d, "b1-003", "STALE", dict(META))["missed"], "1")

    def test_incident_before_injection_is_not_the_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            pre = INC.format(last=0, det=500_000_000, safe=0, kill=0, reg=0, true=0)
            post = INC.format(last=0, det=1_090_000_000, safe=1_100_000_000, kill=0, reg=0, true=0)
            d = run_dir(tmp, "STALE", 1_000_000_000, pre + post)
            row = br.make_row(d, "b1-004", "STALE", dict(META))
            self.assertEqual(row["pre_inject_incidents"], 1)
            self.assertEqual(row["t_detect_ns"], "1090000000")

    def test_starve_uses_the_hog_stamp_and_has_no_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = run_dir(tmp, "STARVE", 2_000_000_000, INC.format(last=0, det=2_040_000_000, safe=2_050_000_000, kill=0, reg=0, true=0), hog=True)
            row = br.make_row(d, "b1-005", "STARVE", dict(META))
            self.assertEqual((row["t_inject_ns"], row["t_restart_ns"], row["t_true_ns"]), (2_000_000_000, "", ""))

    def test_missing_effective_stamp_stays_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "r"
            d.mkdir()
            (d / "incidents.csv").write_text(INC.format(last=0, det=9, safe=0, kill=0, reg=0, true=0))
            self.assertEqual(br.make_row(d, "b1-006", "FREEZE", dict(META))["t_inject_ns"], "")

    def test_diverge_is_derived_from_the_trace_for_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = run_dir(tmp, "FREEZE", 1_000_000_000, INC.format(last=0, det=1_300_000_000, safe=0, kill=0, reg=0, true=0))
            meta = dict(META, slice=[(3.2, 12.5), (3.3, 12.5), (3.4, 12.0), (3.5, 11.0)])   # m/s: 45 km/h then 43.2 at 3.4 s
            row = br.make_row(d, "b1-007", "FREEZE", meta)
            self.assertEqual(row["t_diverge_ns"], 1_000_000_000 + 200_000_000)


class Windows(unittest.TestCase):
    def test_merge_and_threshold(self):
        # 0..5 s steady, braking 5..6 s (10 m/s per s = 3.6 km/h per 100 ms), steady, again at 9 s
        tr = []
        for i in range(0, 1201):
            t = i * 0.01
            v = 20.0
            if 5 <= t < 6:
                v = 20 - 10 * (t - 5)
            elif t >= 6 and t < 9:
                v = 10.0
            elif t >= 9 and t < 10:
                v = 10 - 10 * (t - 9)
            elif t >= 10:
                v = 0.0
            tr.append((t, v))
        self.assertEqual(br.windows(tr, 1.0), [5.0, 9.0])
        self.assertEqual(br.windows(tr, 10.0), [])


class Ablation(unittest.TestCase):
    def test_seconds_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "checks.csv"
            with open(p, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t_cap_start_ns", "decoded", "truth_min", "truth_max", "state"])
                for i in range(0, 20):                       # 100 ms apart, window 1.0 s from t=0.2
                    t = 100_000_000 * i
                    dec = 48 if i < 6 else (-1 if i >= 14 else 5)   # right, then stale (wrong), then blank
                    w.writerow([t, dec, 0 if i >= 6 else 47, 0 if i >= 6 else 49, "RUN"])
            sw, n, last = br.seconds_wrong(p, 200_000_000, 1.0)
            self.assertEqual(n, 10)                                  # checks i=2..11 start inside [0.2 s, 1.2 s)
            self.assertAlmostEqual(sw, 0.6, places=6)                # i=2..5 right (0.4 s), i=6..11 wrong (0.6 s)
            self.assertEqual(last, 1)



class StaleRule(unittest.TestCase):
    def test_standstill_step_is_not_observable_but_braking_is(self):
        tr = [(i * 0.05, 0.0) for i in range(0, 200)]                  # 10 s standing still
        self.assertFalse(br.stale_observable(tr, 2.0))
        tr = [(i * 0.05, 5.0 + 2.0 * i * 0.05) for i in range(0, 200)]  # +2 m/s per s = 7.2 km/h per s
        self.assertTrue(br.stale_observable(tr, 2.0))


if __name__ == "__main__":
    unittest.main()

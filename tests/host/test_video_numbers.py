"""tools/video_numbers.py: per-take 'this run' numbers use the bench's fault stamp (cluster-bench.csv) and 0.1 ms rounding."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import video_numbers  # noqa: E402

INC = ("TT-FRZ-0001,TICK_STALL,hung,48,47,1000101300000,1000123400000,22.1,1000181300000,1000223500000,"
       "1000479800000,1000580700000,1,80.0,11,12,0x01,0x00,1000181300000\n")


class VideoNumbers(unittest.TestCase):
    def run_dir(self, root: Path, name: str, bench: str | None, inc: str | None) -> Path:
        d = root / name
        d.mkdir()
        if bench is not None:
            (d / "cluster-bench.csv").write_text(bench)
        if inc is not None:
            (d / "incidents.csv").write_text(inc)
        return d

    def test_events_after_fault_stamp(self):
        with tempfile.TemporaryDirectory() as t:
            d = self.run_dir(Path(t), "20260101T000000Z-freeze", "1,2,FREEZE,1000000000000,-1,-1\n", INC)
            out = video_numbers.numbers(d)
            self.assertIn("incidents 1", out)
            self.assertIn("last true frame +101.3 ms  caught +123.4 ms  safe layer +181.3 ms  renderer killed +223.5 ms"
                          "  renderer back +479.8 ms  true frame +580.7 ms", out[-1])
            with contextlib.redirect_stdout(io.StringIO()):
                video_numbers.main([str(d)])
            self.assertEqual((d / "numbers.txt").read_text().splitlines(), out)

    def test_disarmed_take_and_missing_stamp(self):
        with tempfile.TemporaryDirectory() as t:
            off = self.run_dir(Path(t), "20260101T000001Z-freeze", "1,2,FREEZE,5,-1,-1\n", None)
            self.assertEqual(video_numbers.numbers(off)[1], "incidents 0")
            nostamp = self.run_dir(Path(t), "20260101T000002Z-stale", None, INC.replace("TICK_STALL", "VALUE_MISMATCH"))
            out = video_numbers.numbers(nostamp)
            self.assertIn("fault effective stamp - ns", out[0])
            self.assertTrue(out[-1].strip().startswith("after the fault: last true frame -  caught -"))


if __name__ == "__main__":
    unittest.main()

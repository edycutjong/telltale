"""Host tests for tools/spike_report.py on hand-written spike outputs (test fixtures, not measurements)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("spike_report", ROOT / "tools" / "spike_report.py")
sr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sr)

G1_PASS = """RESULT route=display tag=plain uid=0 status=EXACT mismatch_px=0 of 80000 read_ns=6000000
RESULT route=window tag=plain uid=0 status=EXACT mismatch_px=0 of 80000 read_ns=2000000
RESULT route=display tag=covered uid=0 status=DIFF mismatch_px=40000 of 80000 read_ns=3000000
RESULT route=window tag=covered uid=0 status=EXACT mismatch_px=0 of 80000 read_ns=2000000
RESULT route=window tag=uid1001-app uid=1001 status=EXACT mismatch_px=0 of 80000 read_ns=2000000
"""
G3_PASS = """policy id:0
policy id:e61229a5 (immutable)
client uid=1000 type=monitor_t(id 1) -> /tmp/tt/v.chan: ConnectAttach OK, MsgSend rc=0 reply='ok'
client uid=1000 type=monitor_t(id 1) -> /tmp/tt/r.chan: ConnectAttach DENIED errno=1 (Operation not permitted)
-- monitor_t asks to drop to uid 1001 (policy grants setuid:1000 only)
setuid: Operation not permitted
"""


def write_run(d: Path, g1=G1_PASS, g3=G3_PASS, timer_err_ms=1.0, check_ms=3.0):
    (d / "g2.txt").write_text("TT-G2 hello from QNX 8.0.0 x86pc, cpu=4\n")
    (d / "g1.txt").write_text(g1)
    (d / "g3.txt").write_text(g3)
    rows = ["i,t_ns,delta_ns,err_ns"] + [f"{i},0,0,{int(timer_err_ms * 1e6) * (-1) ** i}" for i in range(20)]
    (d / "g4-timer.csv").write_text("\n".join(rows) + "\n")
    c = int(check_ms * 1e6)
    rows = ["i,check_ns,lamp_ns,digit_ns,tick_ns"] + [f"{i},{c},{c // 3},{c // 3},{c // 3}" for i in range(20)]
    (d / "g4-readback.csv").write_text("\n".join(rows) + "\n")
    (d / "x.ppm").write_bytes(b"P6\n2 1\n255\n" + bytes([255, 0, 0, 0, 0, 255]))


class TestSpikeReport(unittest.TestCase):
    def test_all_pass_and_png(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d)
            self.assertEqual(sr.main(d), 0)
            s = (d / "SUMMARY.md").read_text()
            self.assertIn("| G1 | PASS (G1-FULL) |", s)
            self.assertIn("| G3 (minimal) | PASS |", s)
            self.assertFalse((d / "x.ppm").exists())
            self.assertTrue((d / "x.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_cover_not_composed_downgrades_to_window_tier(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d, g1=G1_PASS.replace("mismatch_px=40000", "mismatch_px=0").replace("DIFF", "EXACT"))
            self.assertEqual(sr.main(d), 0)
            self.assertIn("| G1 | PASS (G1-WINDOW only) |", (d / "SUMMARY.md").read_text())

    def test_timer_over_bar_fails_g4(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d, timer_err_ms=5.5)
            self.assertEqual(sr.main(d), 3)
            self.assertIn("| G4 | FAIL |", (d / "SUMMARY.md").read_text())

    def test_g3_needs_a_pushed_policy_and_a_denial(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d, g3=G3_PASS.replace("DENIED errno=1 (Operation not permitted)", "OK, MsgSend rc=0"))
            self.assertEqual(sr.main(d), 3)
            d2 = Path(t) / "b"
            d2.mkdir()
            write_run(d2, g3=G3_PASS.replace("policy id:e61229a5 (immutable)", ""))
            self.assertEqual(sr.main(d2), 3)

    def test_g3_uid_drop_must_be_refused(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d, g3=G3_PASS.replace("setuid: Operation not permitted\n", ""))
            self.assertEqual(sr.main(d), 3)
            self.assertIn("| G3 uid drop limited | FAIL |", (d / "SUMMARY.md").read_text())

    def test_refused_rows_show_errno_name_not_pixels(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            write_run(d, g1=G1_PASS + "RESULT route=display tag=uid1001-app uid=1001 status=FAIL errno=13 (Permission denied)\n")
            sr.main(d)
            self.assertIn("| display | uid1001-app | 1001 | refused | — | EACCES | — |", (d / "SUMMARY.md").read_text())

    def test_percentile_is_nearest_rank(self):
        self.assertEqual(sr.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95), 10)
        self.assertEqual(sr.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50), 5)


if __name__ == "__main__":
    unittest.main()

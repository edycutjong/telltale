"""Host tests for tools/fetch_comma_speed.py: range reads against an in-memory zip, no network."""

import http.client
import io
import os
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import fetch_comma_speed as F  # noqa: E402


def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Chunk_1/r|a/3/processed_log/CAN/speed/t", b"T" * 300)
        z.writestr("Chunk_1/r|a/3/processed_log/CAN/speed/value", b"V" * 200)
        z.writestr("Chunk_1/r|b/1/processed_log/CAN/speed/t", b"t")
        z.writestr("Chunk_1/r|b/1/processed_log/CAN/speed/value", b"v")
        z.writestr("Chunk_1/r|a/3/video.hevc", b"X" * 5000)  # not a speed member: must never be extracted
        z.writestr("Chunk_1/r|a/3/processed_log/CAN/steering_angle/t", b"S")
    return buf.getvalue()


class FakeResp:
    def __init__(self, body, status, content_range=""):
        self.body, self.status = body, status
        self.headers = {"Content-Range": content_range}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeServer:
    """Serves byte ranges of `blob`; `fail` is a list of exceptions raised on the first calls, in order."""

    def __init__(self, blob, honour_range=True, fail=(), truncate=False):
        self.blob, self.honour_range, self.fail, self.truncate = blob, honour_range, list(fail), truncate
        self.calls = 0

    def __call__(self, req, timeout=None):
        self.calls += 1
        if self.fail:
            raise self.fail.pop(0)
        if not self.honour_range:
            return FakeResp(self.blob, 200)
        a, b = (int(x) for x in req.headers["Range"].split("=")[1].split("-"))
        body = self.blob[a : b + 1]
        if self.truncate and b > a:
            body = body[:-1]
        return FakeResp(body, 206, f"bytes {a}-{b}/{len(self.blob)}")


def http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, None)


class TestFetch(unittest.TestCase):
    def setUp(self):
        self.blob = make_zip()
        p = mock.patch.object(F.time, "sleep")
        self.sleep = p.start()
        self.addCleanup(p.stop)

    def run_main(self, server, *extra):
        with mock.patch.object(F.urllib.request, "urlopen", server), tempfile.TemporaryDirectory() as d:
            rc = F.main(["--chunk", "1", "--out", d, *extra])
            files = {}
            for root, _, names in os.walk(d):
                for n in names:
                    with open(os.path.join(root, n), "rb") as fh:
                        files[os.path.relpath(os.path.join(root, n), d)] = fh.read()
        return rc, files

    def test_extracts_only_speed_members_with_manifest(self):
        rc, files = self.run_main(FakeServer(self.blob))
        self.assertEqual(rc, 0)
        self.assertEqual(files[os.path.join("Chunk_1", "r_a", "3", "t")], b"T" * 300)
        self.assertEqual(files[os.path.join("Chunk_1", "r_a", "3", "value")], b"V" * 200)
        self.assertNotIn(os.path.join("Chunk_1", "r_a", "3", "video.hevc"), files)
        self.assertEqual(len(files), 5)  # 2 routes x {t,value} + manifest
        manifest = files[os.path.join("Chunk_1", "manifest.csv")].decode().splitlines()
        self.assertEqual(manifest[0], "chunk,route,segment,member,crc32,size")
        self.assertEqual(manifest[1].split(",")[:4], ["1", "r|a", "3", "t"])
        self.assertEqual(manifest[1].split(",")[5], "300")

    def test_route_filter(self):
        rc, files = self.run_main(FakeServer(self.blob), "--route", "r|b")
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(f for f in files if not f.endswith("manifest.csv")),
                         [os.path.join("Chunk_1", "r_b", "1", "t"), os.path.join("Chunk_1", "r_b", "1", "value")])

    def test_unknown_route_returns_2(self):
        rc, files = self.run_main(FakeServer(self.blob), "--route", "nope")
        self.assertEqual(rc, 2)
        self.assertEqual(files, {})

    def test_server_ignoring_range_is_fatal_not_retried(self):
        srv = FakeServer(self.blob, honour_range=False)
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            with self.assertRaisesRegex(OSError, "ignored the Range header"):
                F.RangeFile("http://x/Chunk_1.zip")
        self.assertEqual(srv.calls, 1)

    def test_transient_errors_are_retried_with_backoff(self):
        srv = FakeServer(self.blob, fail=[http_error(503), urllib.error.URLError("reset"), TimeoutError()])
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            rf = F.RangeFile("http://x/Chunk_1.zip")
        self.assertEqual(rf.size, len(self.blob))
        self.assertEqual(rf.retries, 3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [2.0, 4.0, 8.0])

    def test_gives_up_after_retries(self):
        srv = FakeServer(self.blob, fail=[http_error(502)] * F.RETRIES)
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            with self.assertRaises(urllib.error.HTTPError):
                F.RangeFile("http://x/Chunk_1.zip")
        self.assertEqual(srv.calls, F.RETRIES)

    def test_client_errors_are_not_retried(self):
        srv = FakeServer(self.blob, fail=[http_error(404)])
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            with self.assertRaises(urllib.error.HTTPError):
                F.RangeFile("http://x/Chunk_1.zip")
        self.assertEqual(srv.calls, 1)
        self.sleep.assert_not_called()

    def test_short_range_read_is_retried_then_fatal(self):
        srv = FakeServer(self.blob, truncate=True)
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            rf = F.RangeFile("http://x/Chunk_1.zip")  # the 1-byte size probe cannot be short
            with self.assertRaisesRegex(F.ShortReadError, "short range read"):
                rf.read(64)
        self.assertEqual(rf.retries, F.RETRIES - 1)
        self.assertEqual(srv.calls, 1 + F.RETRIES)

    def test_rate_limit_and_dropped_body_are_retried(self):
        srv = FakeServer(self.blob, fail=[http_error(429), http.client.IncompleteRead(b"")])
        with mock.patch.object(F.urllib.request, "urlopen", srv):
            rf = F.RangeFile("http://x/Chunk_1.zip")
        self.assertEqual(rf.retries, 2)

    def test_final_range_is_clamped_to_file_size(self):
        with mock.patch.object(F.urllib.request, "urlopen", FakeServer(self.blob)):
            rf = F.RangeFile("http://x/Chunk_1.zip")
            rf.seek(-10, 2)
            self.assertEqual(rf.read(4096), self.blob[-10:])
            self.assertEqual(rf.read(1), b"")

    def test_longest_run(self):
        self.assertEqual(F.longest_run([]), (0, -1))
        self.assertEqual(F.longest_run([5]), (1, 5))
        self.assertEqual(F.longest_run([9, 1, 2, 3, 7, 8, 10, 11, 3]), (5, 7))
        self.assertEqual(F.longest_run([1, 2, 4, 5]), (2, 1))  # ties keep the first run

    def test_survey_counts_complete_segments_only_and_is_idempotent(self):
        def speed(route, seg, kinds):
            return {f"Chunk_2/{route}/{seg}/processed_log/CAN/speed/{k}": b"x" for k in kinds}
        members = {}
        for seg in (3, 4, 6, 7, 8):  # runs 3-4 and 6-8 -> longest 3 from 6
            members.update(speed("r|c", seg, ("t", "value")))
        members.update(speed("r|c", 5, ("t",)))  # incomplete: would bridge 3-8 into one run of 6 if counted
        members.update(speed("r|d", 1, ("value",)))  # incomplete-only route: not listed at all
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, body in members.items():
                z.writestr(name, body)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "s.csv")
            with open(out, "w") as fh:  # an earlier survey with a stale Chunk_2 row and a Chunk_1 row to keep
                fh.write(F.SURVEY_HEADER + "\n1,r|a,1,1,3\n2,r|old,9,9,1\n")
            with mock.patch.object(F.urllib.request, "urlopen", FakeServer(buf.getvalue())):
                self.assertEqual(F.main(["--chunk", "2", "--survey", out]), 0)
                self.assertEqual(F.main(["--chunk", "2", "--survey", out]), 0)  # re-run replaces, never duplicates
            with open(out) as fh:
                lines = fh.read().splitlines()
        self.assertEqual(lines, [F.SURVEY_HEADER, "1,r|a,1,1,3", "2,r|c,5,3,6"])

    def test_argument_rules(self):
        with mock.patch("sys.stderr", io.StringIO()):
            for argv in (["--chunk", "1"], ["--chunk", "1", "--survey", "s.csv", "--out", "o"],
                         ["--chunk", "1", "--survey", "s.csv", "--route", "r|a"]):
                with self.assertRaises(SystemExit) as cm:
                    F.main(argv)
                self.assertEqual(cm.exception.code, 2, argv)

    def test_safe_name(self):
        self.assertEqual(F.safe_name("99c94dc769b5d96e|2018-09-19--16-09-16"), "99c94dc769b5d96e_2018-09-19--16-09-16")


if __name__ == "__main__":
    unittest.main()

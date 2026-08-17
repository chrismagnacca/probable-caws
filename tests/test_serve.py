"""Unit tests for harness/serve.py — the read-only viewer server.

Run with: python3 -m unittest tests.test_serve -v
"""

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Import serve.py directly (harness/__main__.py may not exist yet while
# other agents work in parallel — do not depend on it).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import serve  # noqa: E402


def make_fixture(root: Path):
    """Build a temp directory mimicking the harness layout."""
    (root / "harness" / "static").mkdir(parents=True)
    (root / "harness" / "static" / "viewer.html").write_text("<html><body>cockpit</body></html>")

    (root / "logs" / "sessions").mkdir(parents=True)
    (root / "state" / "verdicts").mkdir(parents=True)
    (root / "state" / "feedback").mkdir(parents=True)
    (root / "state" / "screenshots" / "F001" / "attempt1").mkdir(parents=True)

    events = [
        {"seq": 1, "ts": "2026-08-16T19:00:00Z", "run_id": "20260816-190000-ab12",
         "event": "run_start", "role": None, "feature_id": None, "attempt": None,
         "session_id": None, "data": {"config": {}, "claude_version": "1.0", "auth_mode": "api_key", "prompt": "build a thing"}},
        {"seq": 2, "ts": "2026-08-16T19:00:05Z", "run_id": "20260816-190000-ab12",
         "event": "session_start", "role": "generator", "feature_id": "F001", "attempt": 1,
         "session_id": "sess-1", "data": {"session_dir": "0001-generator-F001", "model": "claude-sonnet-5"}},
    ]
    events_path = root / "logs" / "events.jsonl"
    with open(events_path, "w") as f:
        for row in events:
            f.write(json.dumps(row) + "\n")

    ledger_rows = [
        {"ts": "2026-08-16T19:00:10Z", "run_id": "20260816-190000-ab12", "session_id": "sess-1",
         "role": "generator", "model": "claude-sonnet-5", "feature_id": "F001", "attempt": 1,
         "input_tokens": 100, "output_tokens": 200, "cache_read_tokens": 0, "cache_creation_tokens": 0,
         "cost_usd": 0.05, "wall_s": 30, "num_turns": 2, "exit_reason": "ok",
         "cumulative_cost_usd": 0.05, "cumulative_tokens": 300},
    ]
    ledger_path = root / "logs" / "ledger.jsonl"
    with open(ledger_path, "w") as f:
        for row in ledger_rows:
            f.write(json.dumps(row) + "\n")

    features = {
        "schema_version": 1, "run_id": "20260816-190000-ab12", "app_summary": "a thing",
        "updated_at": "2026-08-16T19:00:10Z",
        "features": [
            {"id": "F001", "title": "Scaffold", "description": "d", "priority": 1, "depends_on": [],
             "status": "building", "attempts": 1,
             "acceptance_criteria": [{"id": "AC1", "text": "works", "last_verdict": None}],
             "feedback": [], "cost_usd": 0.05, "blocked_reason": None},
        ],
    }
    (root / "state" / "features.json").write_text(json.dumps(features))

    (root / "state" / "verdicts" / "F001.json").write_text(json.dumps(
        {"feature_id": "F001", "attempt": 1, "verdict": "pass",
         "criteria": [{"id": "AC1", "verdict": "pass", "note": "ok"}], "bugs": []}))
    (root / "state" / "feedback" / "F001.md").write_text("Looks good.\n")

    session_dir = root / "logs" / "sessions" / "0001-generator-F001"
    session_dir.mkdir(parents=True)
    (session_dir / "prompt.md").write_text("do the thing")
    transcript_lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
    ]
    (session_dir / "transcript.jsonl").write_text("\n".join(transcript_lines) + "\n")

    shot = root / "state" / "screenshots" / "F001" / "attempt1" / "01-x.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfakepngdata")

    return events_path, ledger_path


class ServeTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmpdir.name)
        cls.events_path, cls.ledger_path = make_fixture(cls.root)

        serve.ViewerHandler.root = cls.root
        cls.httpd = serve.ViewerServer(("127.0.0.1", 0), serve.ViewerHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmpdir.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, headers=None):
        req = urllib.request.Request(self.url(path), headers=headers or {})
        return urllib.request.urlopen(req, timeout=5)


class TestBasicEndpoints(ServeTestBase):
    def test_index_page(self):
        with self.get("/") as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read()
            self.assertIn(b"cockpit", body)
            self.assertEqual(resp.headers.get("Cache-Control"), "no-store")

    def test_api_state(self):
        with self.get("/api/state") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertEqual(data["features"][0]["id"], "F001")

    def test_api_meta_shape(self):
        with self.get("/api/meta") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertIn("root", data)
            self.assertIn("server_time", data)
            self.assertIn("files", data)
            self.assertIn("events.jsonl", data["files"])
            self.assertIn("size", data["files"]["events.jsonl"])
            self.assertIn("mtime", data["files"]["events.jsonl"])

    def test_api_verdict(self):
        with self.get("/api/verdict/F001") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertEqual(data["verdict"], "pass")

    def test_api_feedback(self):
        with self.get("/api/feedback/F001") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b"Looks good", resp.read())

    def test_api_screenshots_listing(self):
        with self.get("/api/screenshots/F001") as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read())
            self.assertEqual(data["attempt1"], ["01-x.png"])

    def test_api_screenshot_png(self):
        with self.get("/api/screenshot/F001/attempt1/01-x.png") as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type"), "image/png")
            self.assertEqual(resp.headers.get("Cache-Control"), "max-age=3600")
            self.assertTrue(resp.read().startswith(b"\x89PNG"))

    def test_api_sessions(self):
        with self.get("/api/sessions") as resp:
            data = json.loads(resp.read())
            self.assertIn("0001-generator-F001", data)

    def test_api_session_prompt(self):
        with self.get("/api/session/0001-generator-F001/prompt.md") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b"do the thing", resp.read())
            self.assertEqual(resp.headers.get("Cache-Control"), "max-age=3600")


class TestPathTraversal(ServeTestBase):
    def _assert_404(self, path):
        try:
            resp = self.get(path)
            self.fail(f"expected 404 for {path}, got {resp.status}")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404, f"expected 404 for {path}, got {e.code}")

    def test_session_dir_traversal(self):
        self._assert_404("/api/session/..%2f..%2fetc/prompt.md")

    def test_bad_fid_verdict(self):
        self._assert_404("/api/verdict/../../etc/passwd")
        self._assert_404("/api/verdict/F1")
        self._assert_404("/api/verdict/FABC")

    def test_bad_fid_feedback(self):
        self._assert_404("/api/feedback/notanid")

    def test_bad_session_dir_prompt(self):
        self._assert_404("/api/session/not-a-valid-dir/prompt.md")

    def test_screenshot_path_traversal(self):
        self._assert_404("/api/screenshot/F001/attempt1/..%2f..%2fprompt.md")
        self._assert_404("/api/screenshot/F001/../attempt1/01-x.png")


class TestTranscriptSlicing(ServeTestBase):
    def test_offset_and_next_offset_header(self):
        with self.get("/api/session/0001-generator-F001/transcript.jsonl?offset=0&limit=65536") as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read()
            next_off = resp.headers.get("X-Next-Offset")
            self.assertIsNotNone(next_off)
            full_size = (self.root / "logs" / "sessions" / "0001-generator-F001" / "transcript.jsonl").stat().st_size
            self.assertEqual(int(next_off), full_size)
            self.assertTrue(body.endswith(b"\n"))

    def test_partial_slice_trims_to_whole_lines(self):
        path = self.root / "logs" / "sessions" / "0001-generator-F001" / "transcript.jsonl"
        first_line_len = len(path.read_bytes().split(b"\n")[0]) + 1
        with self.get(f"/api/session/0001-generator-F001/transcript.jsonl?offset=0&limit={first_line_len}") as resp:
            body = resp.read()
            next_off = int(resp.headers.get("X-Next-Offset"))
            self.assertEqual(next_off, first_line_len)
            self.assertEqual(body.count(b"\n"), 1)

    def test_offset_beyond_eof_returns_empty(self):
        path = self.root / "logs" / "sessions" / "0001-generator-F001" / "transcript.jsonl"
        size = path.stat().st_size
        with self.get(f"/api/session/0001-generator-F001/transcript.jsonl?offset={size+1000}") as resp:
            self.assertEqual(resp.read(), b"")
            self.assertEqual(int(resp.headers.get("X-Next-Offset")), size + 1000)


class TestStateMissingOrBroken(unittest.TestCase):
    def test_state_absent_returns_empty_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "static").mkdir(parents=True)
            (root / "harness" / "static" / "viewer.html").write_text("<html></html>")
            serve.ViewerHandler.root = root
            httpd = serve.ViewerServer(("127.0.0.1", 0), serve.ViewerHandler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(json.loads(resp.read()), {})
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=5)

    def test_state_parse_failure_returns_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "static").mkdir(parents=True)
            (root / "harness" / "static" / "viewer.html").write_text("<html></html>")
            (root / "state").mkdir(parents=True)
            (root / "state" / "features.json").write_text("{not valid json")
            serve.ViewerHandler.root = root
            httpd = serve.ViewerServer(("127.0.0.1", 0), serve.ViewerHandler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5)
                    self.fail("expected HTTPError 503")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 503)
                    self.assertEqual(e.headers.get("Retry-After"), "1")
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=5)


class TestSSEStream(ServeTestBase):
    def _raw_get(self, path, extra_headers=""):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.settimeout(2)
        req = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n{extra_headers}\r\n"
        sock.sendall(req.encode())
        return sock

    def _read_available(self, sock, min_bytes=1, max_wait=3.0):
        chunks = []
        deadline = time.time() + max_wait
        total = 0
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            sock.settimeout(min(0.5, remaining))
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total >= min_bytes:
                break
        return b"".join(chunks)

    def test_initial_replay_and_framing(self):
        sock = self._raw_get("/api/stream?events=0&ledger=0")
        try:
            # Wait out several 0.3s tail-loop iterations so the initial
            # state snapshot AND the seeded events/ledger replay all land,
            # without stopping early on the first (large) state chunk.
            data = self._read_available(sock, min_bytes=10**9, max_wait=2.5)
        finally:
            sock.close()
        text = data.decode("utf-8", errors="replace")
        self.assertIn("HTTP/1.1 200", text)
        self.assertIn("Content-Type: text/event-stream", text)
        self.assertIn("Cache-Control: no-cache", text)
        self.assertIn("event: state", text)
        self.assertIn("event: events", text)
        self.assertIn("event: ledger", text)
        self.assertIn("run_start", text)
        self.assertIn("session_start", text)
        self.assertRegex(text, r"id: e:\d+;l:\d+")

    def test_torn_line_withholding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness" / "static").mkdir(parents=True)
            (root / "harness" / "static" / "viewer.html").write_text("<html></html>")
            (root / "logs").mkdir(parents=True)
            (root / "state").mkdir(parents=True)
            events_path = root / "logs" / "events.jsonl"
            events_path.write_text("")
            (root / "logs" / "ledger.jsonl").write_text("")

            serve.ViewerHandler.root = root
            httpd = serve.ViewerServer(("127.0.0.1", 0), serve.ViewerHandler)
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                sock.settimeout(2)
                sock.sendall(b"GET /api/stream?events=0&ledger=0 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                # drain the initial state snapshot burst
                self._read_available(sock, min_bytes=50, max_wait=1.5)

                complete_row = json.dumps({"seq": 1, "ts": "2026-08-16T19:00:00Z", "event": "warning",
                                            "role": None, "feature_id": None, "attempt": None,
                                            "session_id": None, "data": {}})
                with open(events_path, "a") as f:
                    f.write(complete_row)  # no trailing newline: a torn line
                    f.flush()

                torn_read = self._read_available(sock, min_bytes=1, max_wait=1.2)
                self.assertNotIn(b"event: events", torn_read, "torn line must not be emitted before newline")

                with open(events_path, "a") as f:
                    f.write("\n")
                    f.flush()

                completed_read = self._read_available(sock, min_bytes=10, max_wait=2.0)
                self.assertIn(b"event: events", completed_read)
                self.assertIn(b"warning", completed_read)
                sock.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                t.join(timeout=5)


if __name__ == "__main__":
    unittest.main()

"""Read-only live web viewer ("cockpit") for the probable-caws harness.

Strictly read-only: every file open is 'rb' / read-only. This module never
creates files, takes locks, or sends signals. It does not import any
orchestrator module (and must never be imported from the `run` code path).

Usage:
    python3 -m harness serve [--port 8787] [--root PATH]

See CONTRACTS.md section 10 for the full endpoint/behavior spec this file
implements exactly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DEFAULT_PORT = 8787
# harness/serve.py -> harness/ -> workspace root
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

ALLOWED_EXTENSIONS = {".png", ".md", ".json", ".jsonl", ".html"}

FID_RE = re.compile(r"^F\d{3}$")
ATTEMPT_RE = re.compile(r"^attempt\d+$")
PNG_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.png$", re.IGNORECASE)
SESSION_DIR_RE = re.compile(r"^\d{4}-[a-z]+-F\d{3}$")
LAST_EVENT_ID_RE = re.compile(r"^e:(\d+);l:(\d+)$")

READ_CHUNK = 256 * 1024  # 256KB per tail-loop iteration
MAX_PARTIAL_LINE = 4 * 1024 * 1024  # 4MB oversized-line guard
HEARTBEAT_INTERVAL_S = 10.0
TAIL_POLL_INTERVAL_S = 0.3
DEFAULT_TRANSCRIPT_LIMIT = 65536
MAX_TRANSCRIPT_LIMIT = 4 * 1024 * 1024


# --------------------------------------------------------------------------
# Tailing helper
# --------------------------------------------------------------------------


class Tailer:
    """Stateful byte-offset tail over a single append-only JSONL file.

    Each `poll()` call returns `(status, lines)` where status is one of
    'ok' or 'reset'. `lines` is a list of complete decoded text lines
    (never including the trailing newline). A torn (incomplete) tail line
    is held back across calls until it completes, per contract section 10.
    """

    def __init__(self, path: Path, offset: int = 0):
        self.path = path
        self.offset = max(0, offset)
        self.ino = None
        self.pending = b""

    def poll(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return "ok", []
        except OSError:
            return "ok", []

        reset = False
        if self.ino is not None and st.st_ino != self.ino:
            reset = True
        if st.st_size < self.offset:
            reset = True
        self.ino = st.st_ino

        if reset:
            self.offset = 0
            self.pending = b""
            return "reset", []

        read_pos = self.offset + len(self.pending)
        if st.st_size <= read_pos:
            return "ok", []

        try:
            with open(self.path, "rb") as f:
                f.seek(read_pos)
                chunk = f.read(READ_CHUNK)
        except OSError:
            return "ok", []

        if not chunk:
            return "ok", []

        buf = self.pending + chunk
        last_nl = buf.rfind(b"\n")
        lines = []

        if last_nl == -1:
            # No complete line yet in the accumulated buffer.
            self.pending = buf
            self.offset += len(chunk)
            if len(self.pending) > MAX_PARTIAL_LINE:
                # Oversized-line guard: force-emit truncated.
                text = self.pending.decode("utf-8", errors="replace")
                text = text.replace("\r", "") + "…[truncated]"
                lines.append(text)
                self.pending = b""
            return "ok", lines

        complete = buf[: last_nl + 1]
        remainder = buf[last_nl + 1 :]
        # offset currently points to start-of(pending); advance it to
        # cover exactly the bytes now confirmed complete.
        self.offset = self.offset + len(complete) - len(self.pending)
        self.pending = remainder

        for raw in complete.split(b"\n"):
            if raw == b"":
                continue
            text = raw.decode("utf-8", errors="replace").replace("\r", "")
            lines.append(text)

        return "ok", lines


def parse_last_event_id(value: str):
    m = LAST_EVENT_ID_RE.match(value.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def qint(query: dict, key: str, default: int) -> int:
    try:
        return int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError, IndexError):
        return default


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class ViewerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "probable-caws-viewer/1"
    root: Path = DEFAULT_ROOT

    # -- hygiene -----------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: D401 - silence per-request logs
        pass

    def _safe_file(self, path: Path) -> bool:
        """Extension allowlist + resolve() containment check."""
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            return False
        try:
            root_rp = self.root.resolve()
            rp = path.resolve()
        except OSError:
            return False
        return rp.is_relative_to(root_rp)

    def _send(self, status: int, content_type: str, body: bytes, headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if headers:
                for k, v in headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_404(self, message: bytes = b"not found"):
        self._send(404, "text/plain; charset=utf-8", message)

    # -- dispatch ------------------------------------------------------

    def do_GET(self):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            for regex, handler in ROUTES:
                m = regex.match(path)
                if m:
                    handler(self, query, **m.groupdict())
                    return
            self._send_404()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            try:
                self._send(500, "text/plain; charset=utf-8", b"internal error")
            except Exception:
                pass

    # -- handlers --------------------------------------------------------

    def handle_index(self, query):
        # The page ships with the harness itself; --root only relocates the DATA tree,
        # so resolve viewer.html against this module, not against root.
        path = Path(__file__).resolve().parent / "static" / "viewer.html"
        try:
            data = path.read_bytes()
        except OSError:
            self._send_404(b"viewer.html not found")
            return
        self._send(200, "text/html; charset=utf-8", data, {"Cache-Control": "no-store"})

    def handle_meta(self, query):
        files = {}
        for name, rel in (
            ("events.jsonl", "logs/events.jsonl"),
            ("ledger.jsonl", "logs/ledger.jsonl"),
            ("features.json", "state/features.json"),
        ):
            p = self.root / rel
            try:
                st = p.stat()
                files[name] = {"size": st.st_size, "mtime": st.st_mtime}
            except OSError:
                files[name] = None
        payload = {
            "root": str(self.root),
            "server_time": time.time(),
            "files": files,
        }
        body = json.dumps(payload).encode("utf-8")
        self._send(200, "application/json", body, {"Cache-Control": "no-store"})

    def handle_state(self, query):
        p = self.root / "state" / "features.json"
        try:
            raw = p.read_bytes()
        except OSError:
            self._send(200, "application/json", b"{}", {"Cache-Control": "no-store"})
            return
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            self._send(
                503,
                "application/json",
                b'{"error":"parse failure"}',
                {"Cache-Control": "no-store", "Retry-After": "1"},
            )
            return
        self._send(200, "application/json", raw, {"Cache-Control": "no-store"})

    def handle_verdict(self, query, fid):
        if not FID_RE.match(fid):
            self._send_404()
            return
        p = self.root / "state" / "verdicts" / f"{fid}.json"
        if not self._safe_file(p):
            self._send_404()
            return
        try:
            raw = p.read_bytes()
        except OSError:
            self._send_404()
            return
        self._send(200, "application/json", raw, {"Cache-Control": "no-store"})

    def handle_feedback(self, query, fid):
        if not FID_RE.match(fid):
            self._send_404()
            return
        p = self.root / "state" / "feedback" / f"{fid}.md"
        if not self._safe_file(p):
            self._send_404()
            return
        try:
            raw = p.read_bytes()
        except OSError:
            self._send_404()
            return
        self._send(200, "text/plain; charset=utf-8", raw, {"Cache-Control": "no-store"})

    def handle_screenshots(self, query, fid):
        if not FID_RE.match(fid):
            self._send_404()
            return
        base = self.root / "state" / "screenshots" / fid
        try:
            base_rp = base.resolve()
            if not base_rp.is_relative_to(self.root.resolve()):
                self._send_404()
                return
        except OSError:
            self._send_404()
            return
        result = {}
        try:
            with os.scandir(base) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    files = []
                    try:
                        with os.scandir(entry.path) as it2:
                            for f in it2:
                                if f.is_file() and f.name.lower().endswith(".png"):
                                    files.append(f.name)
                    except OSError:
                        continue
                    files.sort()
                    result[entry.name] = files
        except OSError:
            pass
        body = json.dumps(result).encode("utf-8")
        self._send(200, "application/json", body, {"Cache-Control": "no-store"})

    def handle_screenshot(self, query, fid, attempt, name):
        if not (FID_RE.match(fid) and ATTEMPT_RE.match(attempt) and PNG_NAME_RE.match(name)):
            self._send_404()
            return
        p = self.root / "state" / "screenshots" / fid / attempt / name
        if not self._safe_file(p):
            self._send_404()
            return
        try:
            raw = p.read_bytes()
        except OSError:
            self._send_404()
            return
        self._send(200, "image/png", raw, {"Cache-Control": "max-age=3600"})

    def handle_sessions(self, query):
        base = self.root / "logs" / "sessions"
        names = []
        try:
            with os.scandir(base) as it:
                for e in it:
                    if e.is_dir():
                        names.append(e.name)
        except OSError:
            pass
        names.sort()
        body = json.dumps(names).encode("utf-8")
        self._send(200, "application/json", body, {"Cache-Control": "no-store"})

    def handle_session_prompt(self, query, dir):
        if not SESSION_DIR_RE.match(dir):
            self._send_404()
            return
        p = self.root / "logs" / "sessions" / dir / "prompt.md"
        if not self._safe_file(p):
            self._send_404()
            return
        try:
            raw = p.read_bytes()
        except OSError:
            self._send_404()
            return
        self._send(200, "text/plain; charset=utf-8", raw, {"Cache-Control": "max-age=3600"})

    def handle_session_transcript(self, query, dir):
        if not SESSION_DIR_RE.match(dir):
            self._send_404()
            return
        p = self.root / "logs" / "sessions" / dir / "transcript.jsonl"
        if not self._safe_file(p):
            self._send_404()
            return

        offset = max(0, qint(query, "offset", 0))
        limit = qint(query, "limit", DEFAULT_TRANSCRIPT_LIMIT)
        limit = max(1, min(limit, MAX_TRANSCRIPT_LIMIT))

        try:
            with open(p, "rb") as f:
                f.seek(offset)
                data = f.read(limit)
        except OSError:
            self._send_404()
            return

        if not data:
            body = b""
            next_offset = offset
        else:
            last_nl = data.rfind(b"\n")
            if last_nl == -1:
                # Livelock guard: no newline fits in limit, emit raw bytes
                # anyway and advance the offset regardless.
                body = data
                next_offset = offset + len(data)
            else:
                body = data[: last_nl + 1]
                next_offset = offset + len(body)

        self._send(
            200,
            "text/plain; charset=utf-8",
            body,
            {"Cache-Control": "no-store", "X-Next-Offset": str(next_offset)},
        )

    def handle_stream(self, query):
        # Resume precedence: Last-Event-ID header overrides query params.
        events_off = 0
        ledger_off = 0
        resumed_from_header = False
        last_id = self.headers.get("Last-Event-ID")
        if last_id:
            parsed = parse_last_event_id(last_id)
            if parsed is not None:
                events_off, ledger_off = parsed
                resumed_from_header = True
        if not resumed_from_header:
            events_off = max(0, qint(query, "events", 0))
            ledger_off = max(0, qint(query, "ledger", 0))

        events_path = self.root / "logs" / "events.jsonl"
        ledger_path = self.root / "logs" / "ledger.jsonl"
        state_path = self.root / "state" / "features.json"

        events_tailer = Tailer(events_path, events_off)
        ledger_tailer = Tailer(ledger_path, ledger_off)

        # This response has no Content-Length and is not chunked; the
        # connection must be closed at the end, not kept alive.
        self.close_connection = True

        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

        def cur_id() -> str:
            return f"e:{events_tailer.offset};l:{ledger_tailer.offset}"

        def emit(event_type: str, data_line: str):
            msg = f"id: {cur_id()}\nevent: {event_type}\ndata: {data_line}\n\n"
            self.wfile.write(msg.encode("utf-8"))
            self.wfile.flush()

        def emit_comment(text: str):
            self.wfile.write(f": {text}\n\n".encode("utf-8"))
            self.wfile.flush()

        last_state_stat = None
        last_hb = time.monotonic()

        try:
            # Initial features.json snapshot, always sent once on connect.
            state_bytes = b"{}"
            try:
                st = os.stat(state_path)
                with open(state_path, "rb") as f:
                    raw = f.read()
                json.loads(raw)
                state_bytes = raw
                last_state_stat = (st.st_mtime, st.st_size)
            except (OSError, json.JSONDecodeError):
                pass
            emit("state", state_bytes.decode("utf-8", errors="replace").replace("\r", ""))
            last_hb = time.monotonic()

            while True:
                status, lines = events_tailer.poll()
                if status == "reset":
                    emit("reset", '{"source":"events"}')
                    last_hb = time.monotonic()
                for line in lines:
                    emit("events", line)
                    last_hb = time.monotonic()

                status, lines = ledger_tailer.poll()
                if status == "reset":
                    emit("reset", '{"source":"ledger"}')
                    last_hb = time.monotonic()
                for line in lines:
                    emit("ledger", line)
                    last_hb = time.monotonic()

                try:
                    st = os.stat(state_path)
                    cur_stat = (st.st_mtime, st.st_size)
                except OSError:
                    cur_stat = None
                if cur_stat is not None and cur_stat != last_state_stat:
                    try:
                        with open(state_path, "rb") as f:
                            raw = f.read()
                        json.loads(raw)
                    except (OSError, json.JSONDecodeError):
                        pass
                    else:
                        last_state_stat = cur_stat
                        emit("state", raw.decode("utf-8", errors="replace").replace("\r", ""))
                        last_hb = time.monotonic()

                now = time.monotonic()
                if now - last_hb >= HEARTBEAT_INTERVAL_S:
                    emit_comment("hb")
                    last_hb = now

                time.sleep(TAIL_POLL_INTERVAL_S)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return


# --------------------------------------------------------------------------
# Route table
# --------------------------------------------------------------------------

ROUTES = [
    (re.compile(r"^/$"), ViewerHandler.handle_index),
    (re.compile(r"^/api/stream$"), ViewerHandler.handle_stream),
    (re.compile(r"^/api/state$"), ViewerHandler.handle_state),
    (re.compile(r"^/api/meta$"), ViewerHandler.handle_meta),
    (re.compile(r"^/api/verdict/(?P<fid>[^/]+)$"), ViewerHandler.handle_verdict),
    (re.compile(r"^/api/feedback/(?P<fid>[^/]+)$"), ViewerHandler.handle_feedback),
    (re.compile(r"^/api/screenshots/(?P<fid>[^/]+)$"), ViewerHandler.handle_screenshots),
    (
        re.compile(r"^/api/screenshot/(?P<fid>[^/]+)/(?P<attempt>[^/]+)/(?P<name>[^/]+)$"),
        ViewerHandler.handle_screenshot,
    ),
    (re.compile(r"^/api/sessions$"), ViewerHandler.handle_sessions),
    (
        re.compile(r"^/api/session/(?P<dir>[^/]+)/prompt\.md$"),
        ViewerHandler.handle_session_prompt,
    ),
    (
        re.compile(r"^/api/session/(?P<dir>[^/]+)/transcript\.jsonl$"),
        ViewerHandler.handle_session_transcript,
    ),
]


# --------------------------------------------------------------------------
# Server bootstrap
# --------------------------------------------------------------------------


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness serve", description="Read-only harness viewer.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", type=str, default=None)
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    ViewerHandler.root = root

    try:
        httpd = ViewerServer(("127.0.0.1", args.port), ViewerHandler)
    except OSError:
        print(
            f"viewer: port {args.port} is already in use. Choose another with --port <N>.",
            file=sys.stderr,
        )
        return 1

    actual_port = httpd.server_address[1]
    print(f"viewer: http://127.0.0.1:{actual_port}/")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

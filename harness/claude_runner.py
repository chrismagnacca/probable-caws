"""All `claude -p` subprocess spawn/parse/kill coupling lives here.

Nothing outside this module should ever construct a `claude` command line, parse a
stream-json line, or send a signal to an agent-session process group.
"""

from __future__ import annotations

import dataclasses
import json
import os
import select
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SessionResult:
    ok: bool
    exit_reason: str  # "ok" | "timeout" | "error" | "killed" | "no_result"
    session_id: str
    session_dir: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    num_turns: int
    wall_s: float
    final_text: str


# Exit reasons that are "infra failures": eligible for backoff retry, count toward the
# circuit breaker, never consume a feature attempt.
INFRA_EXIT_REASONS = {"timeout", "error", "killed", "no_result"}


def is_infra_failure(result: SessionResult) -> bool:
    """infra failure = nonzero exit, timeout, no `result` event, `is_error` true.

    Equivalently: anything that isn't a clean "ok" outcome.
    """
    return not result.ok


def backoff_delay_s(retry_index: int, backoff_base_s: int = 30) -> float:
    """`retry_index` is 0-based (0 = first retry). `30 * 4**k` per the contract."""
    return float(backoff_base_s) * (4 ** retry_index)


# ---------------------------------------------------------------------------
# Tolerant stream-json line parsing (pure, unit-testable without a subprocess)
# ---------------------------------------------------------------------------


def parse_line(line: bytes) -> Optional[dict]:
    """Tolerant single-line parse: undecodable lines and non-object JSON are ignored
    (return None), never raise."""
    if not line:
        return None
    try:
        text = line.decode("utf-8", errors="replace").strip()
    except Exception:
        return None
    if not text:
        return None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def parse_result_event(lines: Iterable[bytes]) -> Optional[dict]:
    """Scan raw transcript lines and return the last well-formed `{"type": "result"}`
    event, or None if none was found. Garbage / unknown-type lines are ignored."""
    result_event = None
    for line in lines:
        obj = parse_line(line)
        if obj is not None and obj.get("type") == "result":
            result_event = obj
    return result_event


def build_session_result(
    session_dir,
    lines: Iterable[bytes],
    wall_s: float,
    returncode: Optional[int],
    forced_exit_reason: Optional[str] = None,
) -> SessionResult:
    """Pure construction of a SessionResult from raw transcript lines plus process
    outcome. `forced_exit_reason` overrides classification (used for "timeout"/"killed").
    Every field is read via `.get()` with a default — a malformed/missing `result` event
    never raises.
    """
    result_event = parse_result_event(lines)

    if forced_exit_reason is not None:
        exit_reason = forced_exit_reason
    elif result_event is None:
        exit_reason = "no_result"
    elif result_event.get("is_error"):
        exit_reason = "error"
    elif returncode not in (0, None):
        exit_reason = "error"
    else:
        exit_reason = "ok"

    ok = exit_reason == "ok"
    usage = (result_event or {}).get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    return SessionResult(
        ok=ok,
        exit_reason=exit_reason,
        session_id=str((result_event or {}).get("session_id", "") or ""),
        session_dir=str(session_dir),
        cost_usd=float((result_event or {}).get("total_cost_usd", 0.0) or 0.0),
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        num_turns=int((result_event or {}).get("num_turns", 0) or 0),
        wall_s=float(wall_s),
        final_text=str((result_event or {}).get("result", "") or ""),
    )


# ---------------------------------------------------------------------------
# Command assembly + process spawn/kill
# ---------------------------------------------------------------------------


def build_command(model: str, max_turns: int) -> list:
    return [
        "claude",
        "-p",
        "--verbose",
        "--output-format",
        "stream-json",
        "--model",
        str(model),
        "--max-turns",
        str(max_turns),
        "--permission-mode",
        "acceptEdits",
    ]


def _kill_process_group(proc: subprocess.Popen, grace_s: float) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def run_session(
    role: str,
    feature_id: str,
    prompt_text: str,
    model: str,
    max_turns: int,
    timeout_s: float,
    session_dir,
    workspace_root,
    kill_grace_s: float = 15,
    extra_env: Optional[dict] = None,
    should_abort: Optional[Callable[[], bool]] = None,
) -> SessionResult:
    """Spawn `claude -p`, pipe `prompt_text` via stdin, tee raw stdout lines verbatim to
    `<session_dir>/transcript.jsonl`, and return a `SessionResult`.

    - The exact assembled prompt is saved byte-for-byte to `<session_dir>/prompt.md`
      BEFORE launch.
    - On wall-clock timeout: `os.killpg(pgid, SIGTERM)`, wait `kill_grace_s`, then
      `SIGKILL`. `exit_reason="timeout"`.
    - If `should_abort` is supplied and returns True while the session is running, the
      same kill sequence runs and `exit_reason="killed"`.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = session_dir / "prompt.md"
    prompt_path.write_bytes(prompt_text.encode("utf-8"))

    transcript_path = session_dir / "transcript.jsonl"

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    cmd = build_command(model, max_turns)

    start = time.monotonic()
    deadline = start + float(timeout_s)
    raw_lines: list = []
    timed_out = False
    aborted = False

    with open(prompt_path, "rb") as stdin_f, open(transcript_path, "ab") as transcript_f:
        proc = subprocess.Popen(
            cmd,
            stdin=stdin_f,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(workspace_root),
            start_new_session=True,
            text=False,
            env=env,
        )

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                if should_abort is not None and should_abort():
                    aborted = True
                    break

                ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 0.5))
                if not ready:
                    if proc.poll() is not None:
                        break
                    continue

                chunk = proc.stdout.readline()
                if not chunk:
                    if proc.poll() is not None:
                        break
                    continue

                raw_lines.append(chunk)
                transcript_f.write(chunk)
                transcript_f.flush()
        finally:
            if timed_out or aborted:
                _kill_process_group(proc, kill_grace_s)
            else:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    _kill_process_group(proc, kill_grace_s)
                    timed_out = True
            try:
                if proc.stderr is not None:
                    proc.stderr.close()
            except Exception:
                pass

    wall_s = time.monotonic() - start

    if aborted:
        forced_reason = "killed"
    elif timed_out:
        forced_reason = "timeout"
    else:
        forced_reason = None

    return build_session_result(
        session_dir=session_dir,
        lines=raw_lines,
        wall_s=wall_s,
        returncode=proc.returncode,
        forced_exit_reason=forced_reason,
    )


# ---------------------------------------------------------------------------
# Runner interface (CONTRACTS.md section 4b) — every runner module exposes
# run_session, auth_mode, version, doctor_checks
# ---------------------------------------------------------------------------


def auth_mode() -> str:
    """ANTHROPIC_API_KEY set -> USD-budgeted API billing, else subscription (token budget)."""
    return "api_key" if os.environ.get("ANTHROPIC_API_KEY") else "subscription"


def version() -> str:
    try:
        proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10)
        return (proc.stdout or proc.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def doctor_checks() -> list:
    """[(name, ok, message-or-fix)] — claude CLI present and answering --version."""
    if not shutil.which("claude"):
        return [("claude_cli", False, "install Claude Code and ensure `claude` is on PATH")]
    v = version()
    if v == "unknown":
        return [("claude_cli", False, "`claude --version` failed — reinstall Claude Code")]
    return [("claude_cli", True, f"claude CLI found: {v}")]

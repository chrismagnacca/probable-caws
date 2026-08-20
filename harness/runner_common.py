"""Provider-neutral runner machinery (CONTRACTS.md section 4b): the `SessionResult`
currency, infra-failure classification, tolerant JSONL parsing, and the generic
spawn/tee/timeout-kill subprocess loop every runner shares.

Provider-specific coupling (command lines, event schemas, field mapping) lives in the
per-provider modules: claude_runner.py, codex_runner.py, gemini_runner.py, kimi_runner.py.
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
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Result type — the canonical currency between any runner and the orchestrator
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
    """infra failure = nonzero exit, timeout, no terminal event, provider-reported error.

    Equivalently: anything that isn't a clean "ok" outcome.
    """
    return not result.ok


def backoff_delay_s(retry_index: int, backoff_base_s: int = 30) -> float:
    """`retry_index` is 0-based (0 = first retry). `30 * 4**k` per the contract."""
    return float(backoff_base_s) * (4 ** retry_index)


# ---------------------------------------------------------------------------
# Tolerant JSONL line parsing (pure, unit-testable without a subprocess)
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


# ---------------------------------------------------------------------------
# Process spawn / tee / kill (shared by every runner)
# ---------------------------------------------------------------------------


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


def capture_session(
    cmd: list,
    prompt_text: str,
    session_dir,
    workspace_root,
    timeout_s: float,
    kill_grace_s: float = 15,
    extra_env: Optional[dict] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    prompt_via_stdin: bool = True,
) -> tuple:
    """Spawn `cmd`, optionally feed the prompt via stdin, tee raw stdout lines verbatim
    to `<session_dir>/transcript.jsonl`, and return
    `(raw_lines, returncode, wall_s, forced_exit_reason)`.

    - The exact assembled prompt is saved byte-for-byte to `<session_dir>/prompt.md`
      BEFORE launch, whether or not it is also delivered via stdin.
    - `prompt_via_stdin=False` connects stdin to /dev/null (for CLIs that take the
      prompt as an argument).
    - On wall-clock timeout: `os.killpg(pgid, SIGTERM)`, wait `kill_grace_s`, then
      `SIGKILL`; forced reason "timeout". `should_abort` returning True mid-run runs the
      same kill sequence with forced reason "killed".
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = session_dir / "prompt.md"
    prompt_path.write_bytes(prompt_text.encode("utf-8"))

    transcript_path = session_dir / "transcript.jsonl"

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    start = time.monotonic()
    deadline = start + float(timeout_s)
    raw_lines: list = []
    timed_out = False
    aborted = False

    stdin_f = open(prompt_path, "rb") if prompt_via_stdin else open(os.devnull, "rb")
    with stdin_f, open(transcript_path, "ab") as transcript_f:
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

    return raw_lines, proc.returncode, wall_s, forced_reason


# ---------------------------------------------------------------------------
# Shared doctor/version helpers
# ---------------------------------------------------------------------------


def cli_version(executable: str) -> str:
    try:
        proc = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10)
        return (proc.stdout or proc.stderr or "").strip() or "unknown"
    except Exception:
        return "unknown"


def cli_doctor_check(check_name: str, executable: str, install_fix: str) -> list:
    """Standard [(name, ok, message-or-fix)] check: CLI on PATH and answering --version."""
    if not shutil.which(executable):
        return [(check_name, False, install_fix)]
    v = cli_version(executable)
    if v == "unknown":
        return [(check_name, False, f"`{executable} --version` failed — {install_fix}")]
    return [(check_name, True, f"{executable} CLI found: {v}")]

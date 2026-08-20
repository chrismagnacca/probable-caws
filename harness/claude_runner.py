"""All `claude -p` coupling lives here: command assembly, stream-json event parsing,
and the mapping onto `SessionResult`.

The provider-neutral machinery (SessionResult itself, infra classification, backoff,
tolerant JSONL parsing, and the spawn/tee/timeout-kill loop) lives in `runner_common.py`
and is re-exported here for compatibility — nothing outside this module should ever
construct a `claude` command line or interpret a stream-json event.
"""

from __future__ import annotations

import os
from typing import Callable, Iterable, Optional

from .runner_common import (  # noqa: F401  (re-exported: orchestrator + tests use these)
    INFRA_EXIT_REASONS,
    SessionResult,
    backoff_delay_s,
    capture_session,
    cli_doctor_check,
    cli_version,
    is_infra_failure,
    parse_line,
)

# ---------------------------------------------------------------------------
# stream-json parsing (pure, unit-testable without a subprocess)
# ---------------------------------------------------------------------------


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
# Command assembly + session entry point
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
    `<session_dir>/transcript.jsonl`, and return a `SessionResult`. Spawn/timeout/kill
    semantics per `runner_common.capture_session`."""
    raw_lines, returncode, wall_s, forced_reason = capture_session(
        cmd=build_command(model, max_turns),
        prompt_text=prompt_text,
        session_dir=session_dir,
        workspace_root=workspace_root,
        timeout_s=timeout_s,
        kill_grace_s=kill_grace_s,
        extra_env=extra_env,
        should_abort=should_abort,
        prompt_via_stdin=True,
    )
    return build_session_result(
        session_dir=session_dir,
        lines=raw_lines,
        wall_s=wall_s,
        returncode=returncode,
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
    return cli_version("claude")


def doctor_checks() -> list:
    """[(name, ok, message-or-fix)] — claude CLI present and answering --version."""
    return cli_doctor_check("claude_cli", "claude", "install Claude Code and ensure `claude` is on PATH")

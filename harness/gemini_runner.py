"""Google Gemini CLI runner (`gemini --output-format stream-json`), CONTRACTS.md 4b.

Event mapping authored from the Gemini CLI headless documentation (CLI not installed on
this machine — the fixture in tests/fixtures/gemini_stream_sample.jsonl is the
conformance target until a live session is demonstrated):
  {"type":"init","session_id":"…","model":"…"}
  {"type":"message","role":"assistant","content":"…"}            (delta:true = chunk)
  {"type":"tool_use"…} / {"type":"tool_result"…}
  {"type":"result","status":"success","stats":{"input_tokens":…,"output_tokens":…,
   "total_cost_usd":…?}}

Notes: prompt is delivered via stdin (headless mode). `--yolo` auto-approves tool
calls — required for unattended runs. `max_turns` has no equivalent and is ignored;
the wall-clock timeout is the backstop. `auth_mode()` is "subscription" (token budget):
`total_cost_usd` is optional in stats and is recorded when present, but budgets must
not depend on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from .runner_common import (
    SessionResult,
    capture_session,
    cli_doctor_check,
    cli_version,
    parse_line,
)


def build_command(model: str, max_turns: int) -> list:
    # An empty model string omits -m so the CLI's configured default applies.
    cmd = ["gemini", "--output-format", "stream-json"]
    if model:
        cmd += ["-m", str(model)]
    return cmd + ["--yolo"]


def build_session_result(
    session_dir,
    lines: Iterable[bytes],
    wall_s: float,
    returncode: Optional[int],
    forced_exit_reason: Optional[str] = None,
) -> SessionResult:
    session_id = ""
    final_text = ""
    delta_parts: list = []
    result_event: Optional[dict] = None

    for raw in lines:
        obj = parse_line(raw)
        if obj is None:
            continue
        etype = obj.get("type")
        if etype == "init":
            session_id = str(obj.get("session_id", "") or "")
        elif etype == "message" and obj.get("role") == "assistant":
            content = str(obj.get("content", "") or "")
            if obj.get("delta"):
                delta_parts.append(content)
            elif content:
                final_text = content
                delta_parts = []
        elif etype == "result":
            result_event = obj

    if not final_text and delta_parts:
        final_text = "".join(delta_parts)

    if forced_exit_reason is not None:
        exit_reason = forced_exit_reason
    elif result_event is None:
        exit_reason = "no_result"
    elif str(result_event.get("status", "")) not in ("success", "ok", ""):
        exit_reason = "error"
    elif returncode not in (0, None):
        exit_reason = "error"
    else:
        exit_reason = "ok"

    stats = (result_event or {}).get("stats") or {}
    if not isinstance(stats, dict):
        stats = {}

    return SessionResult(
        ok=exit_reason == "ok",
        exit_reason=exit_reason,
        session_id=session_id or Path(session_dir).name,
        session_dir=str(session_dir),
        cost_usd=float(stats.get("total_cost_usd", 0.0) or 0.0),
        input_tokens=int(stats.get("input_tokens", 0) or 0),
        output_tokens=int(stats.get("output_tokens", 0) or 0),
        cache_read_tokens=int(stats.get("cached_tokens", 0) or 0),
        cache_creation_tokens=0,
        num_turns=0,  # not reported by gemini
        wall_s=float(wall_s),
        final_text=final_text,
    )


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
    return build_session_result(session_dir, raw_lines, wall_s, returncode, forced_reason)


def auth_mode() -> str:
    return "subscription"  # token budget; cost reporting is optional and unreliable


def version() -> str:
    return cli_version("gemini")


def doctor_checks() -> list:
    return cli_doctor_check("gemini_cli", "gemini", "install Gemini CLI and ensure `gemini` is on PATH")

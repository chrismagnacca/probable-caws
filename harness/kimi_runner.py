"""Moonshot Kimi Code CLI runner (`kimi -p … --output-format stream-json`), CONTRACTS.md 4b.

Event mapping authored from the Kimi Code print-mode documentation (CLI installed but
unauthenticated on this machine — the fixture in tests/fixtures/kimi_stream_sample.jsonl
is the conformance target until a live session is demonstrated). Print mode emits
OpenAI-chat-style JSONL: assistant messages (with optional tool_calls) and tool
messages; role/content may sit at the top level or under a "message" key. There is no
documented terminal result event, so success = clean exit with at least one assistant
message.

Notes: the prompt travels as the `-p` argument (print mode does not read a plain-text
prompt from stdin), so stdin is /dev/null. `--auto` = fully autonomous permission mode,
required for unattended runs. `max_turns` has no equivalent and is ignored; the
wall-clock timeout is the backstop. Token usage is captured from any event carrying a
"usage" object (prompt_tokens/completion_tokens or input_tokens/output_tokens); when
Kimi reports none, tokens stay 0 — meaning the token budget cannot bind on a
kimi-only run, which `doctor_checks()` surfaces as an advisory.
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


def build_command(model: str, max_turns: int, prompt_text: str) -> list:
    # An empty model string omits -m so the CLI's configured default applies.
    cmd = ["kimi", "-p", prompt_text, "--output-format", "stream-json"]
    if model:
        cmd += ["-m", str(model)]
    return cmd + ["--auto"]


def _role_and_content(obj: dict) -> tuple:
    message = obj.get("message")
    if isinstance(message, dict):
        return message.get("role"), message.get("content")
    return obj.get("role"), obj.get("content")


def build_session_result(
    session_dir,
    lines: Iterable[bytes],
    wall_s: float,
    returncode: Optional[int],
    forced_exit_reason: Optional[str] = None,
) -> SessionResult:
    session_id = ""
    final_text = ""
    usage: dict = {}

    for raw in lines:
        obj = parse_line(raw)
        if obj is None:
            continue
        if not session_id:
            session_id = str(obj.get("session_id", "") or "")
        role, content = _role_and_content(obj)
        if role == "assistant" and isinstance(content, str) and content:
            final_text = content
        candidate = obj.get("usage")
        if isinstance(candidate, dict):
            usage = candidate

    if forced_exit_reason is not None:
        exit_reason = forced_exit_reason
    elif returncode not in (0, None):
        exit_reason = "error"
    elif not final_text:
        exit_reason = "no_result"
    else:
        exit_reason = "ok"

    return SessionResult(
        ok=exit_reason == "ok",
        exit_reason=exit_reason,
        session_id=session_id or Path(session_dir).name,
        session_dir=str(session_dir),
        cost_usd=0.0,  # kimi never reports USD
        input_tokens=int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
        output_tokens=int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        cache_read_tokens=int(usage.get("cached_tokens", 0) or 0),
        cache_creation_tokens=0,
        num_turns=0,  # not reported by kimi
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
        cmd=build_command(model, max_turns, prompt_text),
        prompt_text=prompt_text,
        session_dir=session_dir,
        workspace_root=workspace_root,
        timeout_s=timeout_s,
        kill_grace_s=kill_grace_s,
        extra_env=extra_env,
        should_abort=should_abort,
        prompt_via_stdin=False,
    )
    return build_session_result(session_dir, raw_lines, wall_s, returncode, forced_reason)


def auth_mode() -> str:
    return "subscription"  # token budget; see module docstring on usage reporting


def version() -> str:
    return cli_version("kimi")


def doctor_checks() -> list:
    checks = cli_doctor_check("kimi_cli", "kimi", "install Kimi Code CLI and ensure `kimi` is on PATH")
    if checks and checks[0][1]:
        checks.append((
            "kimi_budget",
            True,
            "note: if kimi reports no usage events, token budgets cannot bind on kimi-only runs",
        ))
    return checks

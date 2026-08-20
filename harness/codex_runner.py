"""OpenAI Codex CLI runner (`codex exec --json`), CONTRACTS.md section 4b.

Event mapping verified against live output of codex-cli 0.146.0:
  {"type":"thread.started","thread_id":"…"}
  {"type":"turn.started"}
  {"type":"item.completed","item":{"id":"…","type":"agent_message","text":"…"}}
  {"type":"turn.completed","usage":{"input_tokens":…,"cached_input_tokens":…,
   "cache_write_input_tokens":…,"output_tokens":…,"reasoning_output_tokens":…}}

Notes: codex reports tokens, never USD, so `auth_mode()` is always "subscription"
(token budget). `max_turns` has no codex equivalent and is accepted but ignored —
the wall-clock timeout is the backstop. `input_tokens` includes the cached portion
(`cached_input_tokens` is a subset, mapped to cache_read_tokens).
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
    # "-" = read the prompt from stdin. --ephemeral: the harness keeps its own
    # transcript; don't also grow ~/.codex session history. An empty model string
    # omits -m so the CLI's configured default applies.
    cmd = ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
           "--sandbox", "workspace-write"]
    if model:
        cmd += ["-m", str(model)]
    return cmd + ["-"]


def build_session_result(
    session_dir,
    lines: Iterable[bytes],
    wall_s: float,
    returncode: Optional[int],
    forced_exit_reason: Optional[str] = None,
) -> SessionResult:
    session_id = ""
    final_text = ""
    usage: Optional[dict] = None
    num_turns = 0
    errored = False

    for raw in lines:
        obj = parse_line(raw)
        if obj is None:
            continue
        etype = obj.get("type")
        if etype == "thread.started":
            session_id = str(obj.get("thread_id", "") or "")
        elif etype == "turn.completed":
            num_turns += 1
            candidate = obj.get("usage")
            if isinstance(candidate, dict):
                usage = candidate
        elif etype in ("turn.failed", "error"):
            errored = True
        elif etype == "item.completed":
            item = obj.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "agent_message":
                final_text = str(item.get("text", "") or "") or final_text

    if forced_exit_reason is not None:
        exit_reason = forced_exit_reason
    elif errored:
        exit_reason = "error"
    elif returncode not in (0, None):
        exit_reason = "error"
    elif usage is None:
        exit_reason = "no_result"
    else:
        exit_reason = "ok"

    usage = usage if isinstance(usage, dict) else {}
    return SessionResult(
        ok=exit_reason == "ok",
        exit_reason=exit_reason,
        session_id=session_id or Path(session_dir).name,
        session_dir=str(session_dir),
        cost_usd=0.0,  # codex never reports USD
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cached_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_write_input_tokens", 0) or 0),
        num_turns=num_turns,
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
    return "subscription"  # tokens only; codex reports no USD cost


def version() -> str:
    return cli_version("codex")


def doctor_checks() -> list:
    return cli_doctor_check("codex_cli", "codex", "install Codex CLI and ensure `codex` is on PATH")

"""Preflight checks, run automatically at `python3 -m harness run` startup and on demand
via `python3 -m harness doctor`.

Each check returns a `CheckResult(name, ok, warn, message)`. `message` always includes an
actionable fix when `ok` is False. `run()` aborts (returns False) only when a *hard*
failure occurs — a check where `warn=False` and `ok=False` (in practice: a configured
runner's CLI missing, an unknown runner name, or `config.json` unreadable/invalid). Everything else (node/npm, playwright,
git identity, port conflicts, disk space, workspace writability) is advisory: printed as
a warning but never blocks `run`.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class CheckResult:
    name: str
    ok: bool
    warn: bool
    message: str


def detect_auth_mode() -> str:
    """Heuristic: ANTHROPIC_API_KEY set -> "api_key", else "subscription"."""
    return "api_key" if os.environ.get("ANTHROPIC_API_KEY") else "subscription"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _read_app_env_ports(root: Path) -> tuple:
    app_env = Path(root) / "scripts" / "app.env"
    app_port = api_port = None
    if not app_env.exists():
        return app_port, api_port
    try:
        for raw_line in app_env.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line.startswith("APP_PORT="):
                app_port = _safe_int(line.split("=", 1)[1])
            elif line.startswith("API_PORT="):
                api_port = _safe_int(line.split("=", 1)[1])
    except Exception:
        pass
    return app_port, api_port


def _safe_int(text: str) -> Optional[int]:
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _configured_runners(root: Path) -> list:
    """[(runner_name, module_or_None)] for every distinct runner config.json selects.
    Unreadable config -> the default `claude` runner (check_config reports the config error)."""
    from . import orchestrator  # local import: doctor must stay importable standalone
    try:
        config = orchestrator.load_config(root)
    except Exception:
        config = {}
    roles = ["planner", "generator", "evaluator"]
    if orchestrator.review_enabled(config):
        roles.append("reviewer")
    out = []
    for role in roles:
        name = orchestrator.resolve_runner_name(config, role)
        if name not in [n for n, _ in out]:
            out.append((name, orchestrator.RUNNERS.get(name)))
    return out


def check_runners(root: Path) -> list:
    """One CheckResult per runner-provided check; unknown runner name is FATAL."""
    results = []
    for name, module in _configured_runners(root):
        if module is None:
            from . import orchestrator
            results.append(CheckResult(
                f"runner:{name}", False, False,
                f"unknown runner {name!r} in config.json — fix: use one of {sorted(orchestrator.RUNNERS)}",
            ))
            continue
        for check_name, ok, message in module.doctor_checks():
            text = message if ok else f"fix: {message}"
            results.append(CheckResult(f"runner:{name}:{check_name}", ok, False, text))
    return results


def check_auth_mode(root: Path) -> CheckResult:
    from . import orchestrator
    try:
        config = orchestrator.load_config(root)
    except Exception:
        config = {}
    name = orchestrator.resolve_runner_name(config, "default")
    module = orchestrator.RUNNERS.get(name)
    mode = module.auth_mode() if module else detect_auth_mode()
    return CheckResult("auth_mode", True, False, f"auth mode ({name}): {mode}")


def check_node_npm() -> CheckResult:
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node and npm:
        return CheckResult("node_npm", True, True, f"node: {node}, npm: {npm}")
    missing = [name for name, found in (("node", node), ("npm", npm)) if not found]
    return CheckResult(
        "node_npm", False, True,
        f"missing on PATH: {', '.join(missing)} — fix: install Node.js (https://nodejs.org)",
    )


def check_playwright(root: Path) -> CheckResult:
    pkg = Path(root) / "eval" / "node_modules" / "playwright"
    if pkg.is_dir():
        return CheckResult("playwright", True, True, "eval/node_modules/playwright present")
    return CheckResult("playwright", False, True, "playwright not installed — fix: run scripts/bootstrap.sh")


def check_git(root: Path) -> CheckResult:
    git = shutil.which("git")
    if not git:
        return CheckResult("git", False, True, "git not found on PATH — fix: install git")
    app_dir = Path(root) / "app"
    try:
        proc = subprocess.run(
            ["git", "-C", str(app_dir), "config", "user.email"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return CheckResult(
                "git", False, True,
                "git identity not resolvable in app/ — fix: run scripts/bootstrap.sh",
            )
    except Exception:
        return CheckResult(
            "git", False, True,
            "could not verify git identity in app/ — fix: run scripts/bootstrap.sh",
        )
    return CheckResult("git", True, True, "git present with resolvable identity in app/")


def check_ports(root: Path) -> CheckResult:
    app_port, api_port = _read_app_env_ports(root)
    candidates = [p for p in (app_port, api_port, 8787) if p]
    busy = [p for p in candidates if not _port_free(p)]
    if busy:
        return CheckResult(
            "ports", False, True,
            f"port(s) busy: {busy} — fix: free the port(s) or change scripts/app.env / --port",
        )
    return CheckResult("ports", True, True, "configured ports free")


def check_disk(root: Path, min_gb: float = 2.0) -> CheckResult:
    usage = shutil.disk_usage(root)
    free_gb = usage.free / (1024 ** 3)
    if free_gb < min_gb:
        return CheckResult(
            "disk", False, True,
            f"only {free_gb:.1f}GB free — fix: free at least {min_gb:.0f}GB of disk space",
        )
    return CheckResult("disk", True, True, f"{free_gb:.1f}GB free")


def check_workspace_writable(root: Path) -> CheckResult:
    probe = Path(root) / ".doctor_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult("workspace_writable", True, True, f"{root} is writable")
    except Exception as exc:
        return CheckResult(
            "workspace_writable", False, True,
            f"{root} is not writable: {exc} — fix: check filesystem permissions",
        )


def check_config(root: Path) -> CheckResult:
    config_path = Path(root) / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception as exc:
        return CheckResult(
            "config_json", False, False,
            f"config.json unreadable/invalid: {exc} — fix: repair {config_path}",
        )

    prompt_file = Path(root) / config.get("prompt_file", "PROMPT.md")
    if not prompt_file.exists():
        return CheckResult(
            "config_json", False, False,
            f"config.json's prompt_file {prompt_file} does not exist — fix: create it or fix prompt_file",
        )

    return CheckResult("config_json", True, False, "config.json parses and referenced files exist")


ALL_CHECKS = (
    check_runners,
    check_auth_mode,
    check_node_npm,
    check_playwright,
    check_git,
    check_ports,
    check_disk,
    check_workspace_writable,
    check_config,
)

# Checks that take only `root` (vs. no args) — used by run() to dispatch uniformly.
_ROOT_CHECKS = {check_runners, check_auth_mode, check_playwright, check_git, check_ports,
                check_disk, check_workspace_writable, check_config}


def run_checks(root: Path) -> list:
    results = []
    for check_fn in ALL_CHECKS:
        outcome = check_fn(root) if check_fn in _ROOT_CHECKS else check_fn()
        if isinstance(outcome, list):
            results.extend(outcome)
        else:
            results.append(outcome)
    return results


def run(root: Path, verbose: bool = True) -> bool:
    """Run every check; print a human-readable report if `verbose`. Returns True unless
    a hard failure occurred (a check with warn=False and ok=False)."""
    root = Path(root)
    results = run_checks(root)

    hard_fail = False
    for c in results:
        if verbose:
            mark = "✓" if c.ok else "✗"
            severity = "" if c.ok else (" (warn)" if c.warn else " (FATAL)")
            print(f"{mark} {c.name}{severity}: {c.message}")
        if not c.ok and not c.warn:
            hard_fail = True

    if verbose:
        print("doctor: OK" if not hard_fail else "doctor: FAILED (see FATAL checks above)")

    return not hard_fail


def start_caffeinate() -> Optional[subprocess.Popen]:
    """Best-effort: wrap the current process in `caffeinate -dims -w <pid>` so macOS does
    not sleep mid-run. Returns None (silently) if caffeinate is unavailable or fails to
    launch — never raises."""
    if not shutil.which("caffeinate"):
        return None
    try:
        return subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])
    except Exception:
        return None

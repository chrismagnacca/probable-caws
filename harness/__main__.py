"""CLI dispatch: `python3 -m harness <run|doctor|status|serve>`.

Exit code contract: always 0 on a clean halt (the machine-readable stop reason lives in
the `run_end` event and `logs/run.json`); 1 only for startup failures (doctor fail, config
unreadable, lock held, port in use). The `run` path never imports `serve` and vice versa —
`serve` is imported lazily, only inside the `serve` subcommand handler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_root(args) -> Path:
    return Path(args.root).resolve() if getattr(args, "root", None) else WORKSPACE_ROOT


def _cmd_doctor(args) -> int:
    from . import doctor

    root = _resolve_root(args)
    ok = doctor.run(root, verbose=True)
    return 0 if ok else 1


def _cmd_run(args) -> int:
    from . import doctor, orchestrator

    root = _resolve_root(args)

    ok = doctor.run(root, verbose=True)
    if not ok:
        return 1

    caffeinate_proc = doctor.start_caffeinate()
    try:
        orch = orchestrator.Orchestrator(root)
        orch.run(resume=args.resume)
    except orchestrator.OrchestratorLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if caffeinate_proc is not None:
            try:
                caffeinate_proc.terminate()
            except Exception:
                pass
    return 0


def _cmd_status(args) -> int:
    from . import orchestrator

    root = _resolve_root(args)
    print(orchestrator.render_status(root))
    return 0


def _cmd_serve(args) -> int:
    from . import serve  # lazy import: `run` must never import `serve`, and vice versa

    argv = ["--port", str(args.port)]
    if getattr(args, "root", None):
        argv += ["--root", args.root]
    return serve.main(argv)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="doctor preflight, then run the orchestrator loop")
    p_run.add_argument("--resume", action="store_true",
                        help="alias; recovery is automatic and coarse regardless of this flag")
    p_run.add_argument("--root", default=None, help="workspace root (default: repo root)")
    p_run.set_defaults(func=_cmd_run)

    p_doctor = sub.add_parser("doctor", help="preflight checks only, human-readable report")
    p_doctor.add_argument("--root", default=None)
    p_doctor.set_defaults(func=_cmd_doctor)

    p_status = sub.add_parser("status", help="one-screen status table")
    p_status.add_argument("--root", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_serve = sub.add_parser("serve", help="read-only web viewer")
    p_serve.add_argument("--port", type=int, default=8787)
    p_serve.add_argument("--root", default=None)
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

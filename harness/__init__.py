"""probable-caws: a stdlib-only harness for long-running agentic coding.

See CONTRACTS.md (workspace-external scratchpad) for the authoritative spec. This package
is intentionally free of any submodule imports here to avoid import-order surprises and to
keep `python3 -m harness serve` from ever pulling in orchestrator/claude_runner (and vice
versa).
"""

__all__ = ["claude_runner", "doctor", "orchestrator"]

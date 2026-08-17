# probable-caws

A harness for long-running, unattended agentic coding. You write a few sentences describing an
app; a **Planner** agent turns that into a spec and a feature backlog; then, one feature at a
time, a **Generator** agent implements it and an **Evaluator** agent black-box tests it through the
running app's UI — pass, and the harness commits + tags the code and moves on; fail, and the
Generator gets another attempt with the Evaluator's feedback. The loop runs unattended for hours,
with every decision, cost, and screenshot recorded to disk so you can watch it live or reconstruct
exactly what happened afterward.

This design follows the ideas in Anthropic's
[*Effective harness design for long-running agentic coding*](https://www.anthropic.com/engineering/harness-design-long-running-apps):
serial, memory-less agent sessions coordinating entirely through files, deterministic
orchestration around the LLM calls rather than clever agent-side judgment, and an observability
layer that treats the run itself as a product.

## Quickstart

```bash
# 1. Describe the app you want (1-4 sentences is enough)
$EDITOR PROMPT.md

# 2. One-time setup: git identities, both repos, eval/ dependencies + Chromium
bash scripts/bootstrap.sh

# 3. Run the loop (preflights itself, then plans + builds + evaluates until done or stopped)
python3 -m harness run
```

Watch it live, in either terminal:

```bash
python3 -m harness serve            # cockpit at http://127.0.0.1:8787
tail -f logs/events.jsonl | jq      # raw event stream
```

## Shell shortcuts

Add to `~/.zshrc`:

```bash
source ~/Projects/probable-caws/scripts/aliases.zsh
```

Then, from any directory: `caws run`, `caws up` (cockpit in the background + browser tab;
`caws down` stops it), `caws st`, `caws stop`, `caws logs`, `caws cost`,
`caws rollback F007`, `caws bootstrap`, `caws cd` — `caws help` lists everything.
`caws run` also clears a stale `state/STOP` sentinel so a previous halt can't end the new
run immediately. Tab completion included (`caws rollback <TAB>` completes checkpoint tags).

## The loop

```
                         ┌─────────────────────────────────────────────┐
                         │                 once, at start               │
                         │                                               │
PROMPT.md ──────────────▶│   Planner   ──▶ spec.md + backlog + app.env  │
                         └─────────────────────────────────────────────┘
                                                │
                                                ▼
                         ┌─────────────────────────────────────────────┐
                         │        per feature (until backlog done)      │
                         │                                               │
                         │  compile contract.md (deterministic, no LLM) │
                         │              │                                │
                         │              ▼                                │
                         │         Generator  ──▶ edits app/, handoff.md │
                         │              │                                │
                         │              ▼                                │
                         │      check.sh (install + lint/test)           │
                         │              │                                │
                         │              ▼                                │
                         │   stop.sh → start.sh → poll healthcheck.sh    │
                         │              │                                │
                         │              ▼                                │
                         │         reset.sh → Evaluator (black-box)      │
                         │              │                                │
                         │              ▼                                │
                         │           verdict.json                        │
                         │         ┌────┴────┐                           │
                         │       pass       fail (retry, max 3 attempts) │
                         │         │          │                          │
                         │         ▼          ▼                          │
                         │  git commit +   feedback → next Generator     │
                         │  tag good/F###   attempt, or blocked          │
                         │         │                                     │
                         │         └──────▶ next eligible feature        │
                         └─────────────────────────────────────────────┘
                                                │
                                          all done / stuck /
                                       budget or time exhausted
                                                ▼
                                         run_end (stop reason)
```

`stop.sh` always runs in a `finally` block, so the app is never left running between iterations.

## CLI reference

| Command | What it does |
| --- | --- |
| `python3 -m harness run [--resume]` | Preflight (`doctor`), then loop until a stop condition. `--resume` recovers from a prior interrupted run: demotes anything stuck in `building` back to `failed` (no attempt consumed), resets a dirty `app/` tree to its last good tag, reinstalls, and continues. |
| `python3 -m harness doctor` | Preflight checks only — human-readable ✓/✗ report. Exit 0/1. |
| `python3 -m harness status` | One-screen snapshot: per-feature status/attempts/cost, current phase, spend vs. budget, last 5 events, stop reason if the run ended. |
| `python3 -m harness serve [--port 8787] [--root PATH]` | Starts the read-only viewer cockpit. Never writes anything. |

Exit code is always `0` on a clean halt (the reason is in `run_end` / `logs/run.json`); `1` only
for startup failures (doctor fail, unreadable config, lock held, port in use).

## Stopping, resuming, rolling back

- **Stop gracefully:** `touch state/STOP` — the harness finishes its current session and halts
  cleanly at the next safe point (`stop_reason: HUMAN_STOP`).
- **Resume:** `python3 -m harness run --resume` (or just `run` again — recovery is automatic and
  coarse either way).
- **Roll back a feature's code:** every passing feature is tagged in the `app/` repo, so
  ```bash
  git -C app reset --hard good/F007
  git -C app clean -fd
  bash scripts/check.sh --install-only
  ```
  restores the app tree to the last known-good state after feature `F007`. This never touches
  `state/`, `logs/`, or the root repo's history — the record of what happened is preserved even if
  the code is rolled back.

## Reading a post-mortem (the file trail)

Everything the harness and its agents did is on disk, in order:

1. `logs/run.json` / `logs/STATUS.md` — what happened, at a glance.
2. `logs/events.jsonl` — the full timeline (`session_start`, `eval_verdict`, `git_checkpoint`, …).
3. `logs/ledger.jsonl` — every session's cost/tokens/duration, with running cumulative totals.
4. `logs/sessions/<seq>-<role>-<F###>/prompt.md` — the exact prompt a given session received.
5. `logs/sessions/<seq>-<role>-<F###>/transcript.jsonl` — that session's full raw output.
6. `state/verdicts/F###.json` + `state/feedback/F###.md` + `state/screenshots/F###/attemptN/` —
   what the Evaluator checked, why it passed or failed, and the visual evidence.
7. `state/decisions.md` / `state/handoff.md` — the durable facts and notes agents left for each
   other.

The viewer (`python3 -m harness serve`) replays this same trail as a live cockpit: a vitals strip,
a swimlane timeline of every session, a live transcript tail of whatever's running right now, a
per-feature board, and a per-feature "forensics" view joining the verdict, feedback, and
screenshots for each attempt.

## Configuration (`config.json`)

| Key | Meaning |
| --- | --- |
| `prompt_file` | Path to the human's app request. |
| `models.planner` / `.generator` / `.evaluator` | Model per role. |
| `budget.max_usd` / `.max_tokens` / `.warn_fraction` | Spend cap (USD for API-key auth, tokens for subscription auth) and the fraction at which a warning is emitted. |
| `attempts.max_per_feature` | Retries per feature before it's marked `failed`. |
| `attempts.max_consecutive_infra_failures` | Circuit breaker: consecutive infra failures (timeouts, crashes) before halting. |
| `attempts.max_api_retries` / `.backoff_base_s` | Backoff retry count/base for infra failures (`backoff_base_s * 4^k`). |
| `timeouts.planner_s` / `.generator_s` / `.evaluator_s` / `.precheck_s` | Wall-clock timeout per session type. |
| `timeouts.boot_s` / `.first_boot_s` / `.boot_poll_interval_s` | App boot polling: normal, first-ever boot (no `good/F###` tag yet), and poll interval. |
| `timeouts.kill_grace_s` | Grace period between `SIGTERM` and `SIGKILL` on session timeout. |
| `max_turns.planner` / `.generator` / `.evaluator` | Max agent turns per session. |
| `run.max_wall_hours` / `.max_iterations` | Overall run limits. |
| `planner_bounds.min_features` / `.max_features` / `.min_criteria` / `.max_criteria` | Backlog shape constraints validated on the Planner's output. |
| `handoff.max_blocks` | How many recent Generator handoff blocks are kept/injected into contracts. |
| `screenshots.keep_attempts` | How many recent attempts' screenshots are retained per feature. |
| `viewer.port` | Default port for `serve`. |

## Design, in brief

1. **Files, not memory, coordinate agents.** Every `claude -p` session is a fresh process with no
   conversation history; the only channel between sessions is what's written to `state/`, `app/`,
   and `logs/`.
2. **The orchestrator is deterministic; the LLM is not.** Feature selection, contract compilation,
   retry/backoff, budget gating, and git operations are all plain Python — no LLM call decides
   control flow.
3. **Black-box evaluation.** The Evaluator only ever drives the running app through its UI/HTTP
   surface, the same way a real user would — never by reading the source it's grading.
4. **Two git repos, cleanly separated.** Repo #1 (this workspace) is the audit trail of what the
   harness and agents did; repo #2 (`app/`) is just the product code, rollback-able independently
   without touching the record of how it got there.
5. **Observability is a first-class feature.** Every session, cost, verdict, and screenshot is
   recorded as it happens, in formats meant to be tailed, diffed, and replayed — not just logged
   for debugging.
6. **Fail safe, not fail silent.** Budget caps, wall-clock limits, a circuit breaker on infra
   failures, and a `state/STOP` sentinel all exist so a run degrades to a clean, explained halt
   instead of spinning forever or draining a budget unnoticed.

## Security limits (honest version)

There is **no OS-level sandbox** here — on macOS, agent sessions run as your own user with your
own filesystem and network access. Containment is two things only: the permission policy in
`.claude/settings.json` (a scoped allow-list for Edit/Write/Bash, plus a short deny-list for the
obviously dangerous stuff — `git push`, `sudo`, `rm -rf /`, SSH/AWS credential reads, `WebFetch`)
and the fact that all code changes land in a git repo you can diff and roll back. This is real but
partial protection: a sufficiently motivated or confused agent session can still do things inside
the allow-list you wouldn't want. If you need stronger isolation, the natural graduation path is
running the harness as a **dedicated, unprivileged local user** (or in a container/VM) with its
own filesystem permissions, rather than trusting the allow-list alone.

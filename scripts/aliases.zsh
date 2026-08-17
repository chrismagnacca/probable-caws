# probable-caws harness shortcuts.
#
# Add to ~/.zshrc:
#   source ~/Projects/probable-caws/scripts/aliases.zsh
#
# All commands work from any directory. Override the workspace location with
#   export CAWS_ROOT=/path/to/probable-caws
# before sourcing if the repo ever moves.

export CAWS_ROOT="${CAWS_ROOT:-$HOME/Projects/probable-caws}"

caws() {
  local root="$CAWS_ROOT"
  if [[ ! -d "$root/harness" ]]; then
    echo "caws: harness not found at $root (set CAWS_ROOT)" >&2
    return 1
  fi
  local cmd="${1:-help}"
  (( $# > 0 )) && shift

  case "$cmd" in
    run)
      # A stale STOP sentinel from a previous halt would end the run immediately.
      if [[ -f "$root/state/STOP" ]]; then
        rm -f "$root/state/STOP"
        echo "caws: cleared stale state/STOP"
      fi
      ( cd "$root" && python3 -m harness run "$@" )
      ;;

    doctor)  ( cd "$root" && python3 -m harness doctor "$@" ) ;;
    st|status) ( cd "$root" && python3 -m harness status "$@" ) ;;

    serve)   ( cd "$root" && python3 -m harness serve "$@" ) ;;

    up)
      # Viewer in the background + browser tab. `caws down` stops it.
      local port
      port="$(python3 -c "import json;print(json.load(open('$root/config.json')).get('viewer',{}).get('port',8787))")"
      if [[ -f "$root/state/.tmp/viewer.pid" ]] && kill -0 "$(cat "$root/state/.tmp/viewer.pid")" 2>/dev/null; then
        echo "caws: viewer already running (http://127.0.0.1:$port/)"
      else
        mkdir -p "$root/state/.tmp" "$root/logs"
        ( cd "$root" && nohup python3 -m harness serve --port "$port" >> logs/viewer.log 2>&1 &
          echo $! > state/.tmp/viewer.pid )
        sleep 1
        if kill -0 "$(cat "$root/state/.tmp/viewer.pid")" 2>/dev/null; then
          echo "caws: viewer up — http://127.0.0.1:$port/"
        else
          echo "caws: viewer failed to start — tail $root/logs/viewer.log" >&2
          return 1
        fi
      fi
      open "http://127.0.0.1:$port/"
      ;;

    down)
      if [[ -f "$root/state/.tmp/viewer.pid" ]]; then
        kill "$(cat "$root/state/.tmp/viewer.pid")" 2>/dev/null && echo "caws: viewer stopped"
        rm -f "$root/state/.tmp/viewer.pid"
      else
        echo "caws: no viewer pidfile — nothing to stop"
      fi
      ;;

    stop)
      touch "$root/state/STOP"
      echo "caws: STOP requested — the run halts gracefully after the current session"
      ;;

    logs)
      if command -v jq >/dev/null 2>&1; then
        tail -n 20 -f "$root/logs/events.jsonl" | jq -r '[.ts, .event, .feature_id // "-", (.data.exit_reason // .data.stop_reason // "")] | @tsv'
      else
        tail -n 20 -f "$root/logs/events.jsonl"
      fi
      ;;

    cost)
      if [[ -f "$root/logs/ledger.jsonl" ]]; then
        tail -1 "$root/logs/ledger.jsonl" | python3 -c "import json,sys; r=json.load(sys.stdin); print(f\"\${r['cumulative_cost_usd']:.2f} / {r['cumulative_tokens']:,} tokens (last: {r['role']} {r['feature_id']} \${r['cost_usd']:.2f})\")"
      else
        echo "caws: no ledger yet"
      fi
      ;;

    rollback)
      if [[ -z "${1:-}" ]]; then
        echo "usage: caws rollback <F###|BASELINE>" >&2
        ( cd "$root" && git -C app tag -l 'good/*' )
        return 1
      fi
      ( cd "$root" \
        && git -C app reset --hard "good/$1" \
        && git -C app clean -fd \
        && bash scripts/check.sh --install-only )
      ;;

    bootstrap) ( cd "$root" && bash scripts/bootstrap.sh ) ;;

    cd) cd "$root" ;;

    help|*)
      cat <<'EOF'
caws — probable-caws harness shortcuts
  caws run         start the harness loop (clears a stale STOP first)
  caws doctor      preflight checks
  caws st[atus]    one-screen run status
  caws up          cockpit in background + open browser  (caws down to stop)
  caws serve       cockpit in foreground
  caws stop        request a graceful halt (touch state/STOP)
  caws logs        follow the event stream (pretty if jq installed)
  caws cost        spend so far, from the ledger
  caws rollback F007   reset app/ to a passing checkpoint (+ reinstall deps)
  caws bootstrap   one-time setup (git repos, baseline tag, playwright)
  caws cd          cd into the workspace
EOF
      ;;
  esac
}

# Tab completion (no-op if compinit isn't loaded yet).
if command -v compdef >/dev/null 2>&1; then
  _caws() {
    if (( CURRENT == 2 )); then
      compadd run doctor status st serve up down stop logs cost rollback bootstrap cd help
    elif [[ "${words[2]}" == "rollback" ]]; then
      compadd BASELINE ${(f)"$(git -C "$CAWS_ROOT/app" tag -l 'good/*' 2>/dev/null | sed 's|^good/||')"}
    fi
  }
  compdef _caws caws
fi

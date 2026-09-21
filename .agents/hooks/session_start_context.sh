#!/usr/bin/env bash
# SessionStart hook: inject a compact repo-state line into context.
#
# Observation-only (always exits 0). Scoped to the vibe-agent checkout
# containing this script (worktrees included); exits silently elsewhere.
#
# Register in ~/.kimi-code/config.toml (hooks are user-level only — there is
# no project-level config mechanism):
#
#     [[hooks]]
#     event = "SessionStart"
#     command = "bash /Users/rsong/DevSpace/vibe-agent/.agents/hooks/session_start_context.sh"
#     timeout = 5

set -u

payload=$(cat)
cwd=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null) || exit 0

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$script_dir/../.." && pwd)"

case "$cwd/" in
  "$repo/"*) ;;
  *) exit 0 ;;
esac

cd "$cwd" 2>/dev/null || exit 0
branch=$(git branch --show-current 2>/dev/null || echo "?")
dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
echo "[vibe-agent] branch=$branch dirty_files=$dirty — conventions in AGENTS.md; security changes need tests in tests/tools/security/"
exit 0

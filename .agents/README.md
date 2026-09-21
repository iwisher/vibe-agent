# vibe-agent — Agent Project Customizations (Antigravity & Kimi Code)

Project-level customization for AI coding agents (Google Antigravity and Kimi Code CLI).

| What | Where | Antigravity Discovery | Kimi Code Discovery |
|---|---|---|---|
| **Custom sub-agents** | `.agents/agents/*.md` | Auto-discovered at project scope | Auto-discovered at project scope |
| **Workspace skills** | `.agents/skills/*/SKILL.md` | Auto-discovered (progressive disclosure) | Auto-discovered in project scope |
| **Lifecycle hooks** | `.agents/hooks.json` | **Auto-discovered** (project-level config) | N/A (requires manual `~/.kimi-code/config.toml`) |
| **Hook scripts** | `.agents/hooks/*` | Executed via `hooks.json` | Executed via `~/.kimi-code/config.toml` |

## Agents

- `security-reviewer` — reviews security-layer changes against the repo's
  fail-closed / veto-only / fencing invariants; severity-ranked report.
  Use: "dispatch the security-reviewer on the current diff" after touching
  `vibe/tools/`, `vibe/redteam/`, or the approval pipeline.
- `test-runner` — picks the minimal pytest subset for a change set, runs it
  with the repo venv (worktree-aware), plus `ruff check` / `ruff format --check`
  on changed files.

## Hooks

Scripts are self-scoping and fail-open (always exit 0).

- `post_edit_lint.py` — `PostToolUse` on editing tools (`replace_file_content|write_to_file` in Antigravity; `Edit|Write` in Kimi): runs `ruff check` on the touched `.py` file and reports findings.
- `session_start_context.sh` — `SessionStart`: appends a one-line repo state (branch, dirty file count) to context.

### Antigravity Setup (Zero Manual Config)

Antigravity natively reads `.agents/hooks.json` at the project root automatically. No manual registration is required.

### Kimi Code Setup (User-Level Config)

Add to `~/.kimi-code/config.toml`:

```toml
[[hooks]]
event = "PostToolUse"
matcher = "Edit|Write"
command = "python3 /Users/rsong/DevSpace/vibe-agent/.agents/hooks/post_edit_lint.py"
timeout = 10

[[hooks]]
event = "SessionStart"
command = "bash /Users/rsong/DevSpace/vibe-agent/.agents/hooks/session_start_context.sh"
timeout = 5
```

## Trust note

Project-scoped agent files and hook definitions are configuration that ships with the repo.
Review them with the same caution as scripts when cloning unfamiliar projects.

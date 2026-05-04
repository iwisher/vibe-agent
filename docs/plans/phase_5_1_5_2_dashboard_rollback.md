# Phase 5.1 + 5.2 Implementation Plan

## Phase 5.1: React Trace Dashboard

### Goal
Provide a web-based observability interface for vibe-agent sessions, wiki knowledge, skills, and telemetry.

### Architecture
- **Backend**: FastAPI serving REST API + WebSocket for live updates
- **Frontend**: React SPA (single HTML file with embedded JS/CSS for simplicity — no build step)
- **Data sources**: SessionStore (SQLite), TraceStore (SQLite), Wiki (markdown files), TelemetryCollector

### Files to create
1. `vibe/dashboard/server.py` — FastAPI app with endpoints:
   - `GET /api/sessions` — list sessions with state, duration, model
   - `GET /api/sessions/{id}/timeline` — message timeline with tool calls
   - `GET /api/wiki` — wiki page list with tags and verification status
   - `GET /api/telemetry` — aggregated telemetry (latency, token usage, errors)
   - `GET /api/skills` — installed skills with execution history
   - `WebSocket /ws/live` — push live session updates

2. `vibe/dashboard/static/index.html` — React SPA (CDN-loaded React + D3):
   - Session timeline view (message flow, state transitions)
   - Wiki knowledge graph (D3.js force-directed graph of page links)
   - Skill waterfall (Gantt-like chart of skill execution)
   - Telemetry dashboard (latency histograms, token usage charts)
   - Live session monitor (WebSocket-connected real-time view)

3. `vibe/dashboard/static/app.js` — React components:
   - `App` — routing between views
   - `SessionTimeline` — vertical timeline with state badges
   - `WikiGraph` — D3 force-directed graph
   - `SkillWaterfall` — execution timeline
   - `TelemetryPanel` — charts via Chart.js (CDN)
   - `LiveMonitor` — WebSocket consumer

4. `vibe/cli/main.py` — add `vibe dashboard` command:
   - Launch FastAPI server on configurable port (default 8080)
   - Open browser automatically
   - Background mode support

### Data flow
```
SessionStore (SQLite) → FastAPI /api/sessions
TraceStore (SQLite) → FastAPI /api/sessions/{id}/timeline
Wiki (.md files) → FastAPI /api/wiki
TelemetryCollector → FastAPI /api/telemetry
Skill registry → FastAPI /api/skills
QueryLoop (live) → WebSocket /ws/live
```

### Security
- CORS restricted to localhost
- No authentication (local-only dashboard)
- Read-only API (no mutations via dashboard)

---

## Phase 5.2: Shadow Workspace Rollbacks

### Goal
Before any write-heavy task, create a hidden git branch. If the task fails (ERROR/INCOMPLETE), offer `vibe rollback` to restore the workspace.

### Architecture
- **ShadowBranchManager**: creates `vibe/shadow-<session-id>` branches
- **Integration**: hooks into ToolExecutor before write operations
- **CLI**: `vibe rollback` restores from latest shadow branch

### Files to create
1. `vibe/tools/git_shadow.py` — ShadowBranchManager:
   - `create_shadow(session_id)` — stash changes, create branch, apply stash
   - `restore_shadow(session_id)` — checkout shadow branch, merge back
   - `list_shadows()` — list all vibe/shadow-* branches
   - `clean_shadows(older_than_days=7)` — cleanup old shadows

2. `vibe/core/coordinators.py` — integrate into ToolExecutor:
   - Before write-heavy tool calls (file_write, bash with rm/mv, git operations), create shadow
   - On ERROR/INCOMPLETE state, mark shadow as "restorable"

3. `vibe/cli/main.py` — add `vibe rollback` command:
   - List restorable shadows
   - Restore selected shadow
   - Clean old shadows

### Shadow branch naming
```
vibe/shadow-<session-id>  # e.g., vibe/shadow-sess-abc123
```

### Rollback flow
```
1. ToolExecutor detects write-heavy operation
2. ShadowBranchManager.create_shadow(session_id)
3. Operation executes
4. If state → ERROR/INCOMPLETE:
   a. Mark shadow as restorable
   b. Log rollback availability
5. User runs `vibe rollback`
6. ShadowBranchManager.restore_shadow(session_id)
7. Workspace restored to pre-operation state
```

### Integration with Phase 3.2 SessionStore
- Shadow branch metadata stored in SessionStore checkpoint
- On resume, check if shadow branch exists and is restorable

---

## Phase D: Integration & Final Review

1. Full test suite run (all tests)
2. Gemini CLI bulk review across 5.1 + 5.2
3. Fix critical issues
4. Update ROADMAP.md
5. Final commit

## Test plan
- `tests/test_dashboard_api.py` — FastAPI endpoint tests
- `tests/test_shadow_branch.py` — git shadow/restore tests
- `tests/test_rollback_cli.py` — CLI command tests

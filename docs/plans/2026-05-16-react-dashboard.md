# Plan: React Trace Dashboard (v0.3.4)

## Overview
Build a FastAPI + React web dashboard served via `vibe dashboard` CLI command.

## Architecture
```
vibe/dashboard/
├── api.py          # FastAPI app with endpoints
├── data.py         # Data access layer (TraceStore, Wiki, Skills, Telemetry)
├── __init__.py
└── static/         # React build output (served by FastAPI)
    ├── index.html
    ├── app.js      # React app with D3.js charts
    └── style.css   # Dark theme
```

## API Endpoints
- `GET /api/sessions` — List sessions with pagination (query: `?limit=50&offset=0&status=error`)
- `GET /api/sessions/{id}` — Session detail with messages
- `GET /api/sessions/stream` — Server-Sent Events for live session updates
- `GET /api/wiki/pages` — List wiki pages
- `GET /api/wiki/graph` — Entity graph nodes+edges
- `GET /api/skills` — Installed skills
- `GET /api/telemetry` — Aggregated telemetry (token usage, latency, cost)
- `GET /api/stats` — Summary counts
- `GET /api/config` — Current vibe configuration

## Security
- Bind to `127.0.0.1` by default (NOT `0.0.0.0`)
- Strict CORS: only allow same-origin requests
- No write endpoints (read-only dashboard)
- Optional: auto-generated bearer token for future write operations

## Frontend Architecture
- **Vite** bundler for React → static files
- **Recharts** for telemetry charts (line/bar)
- **D3.js** for WikiGraph only (encapsulated in `useEffect` + ref)
- Dark theme CSS

## Data Access Layer
- `DashboardDataSource` class wraps:
  - `TraceStore.get_sessions()` — sync, run in threadpool
  - `LLMWiki.list_pages()` — async
  - `WikiGraph` — sync (SQLite)
  - `SkillInstaller.list_installed()` — sync
  - `TelemetryCollector.get_summary()` — sync
- FastAPI routes are `async def`, sync calls use `run_in_threadpool`

## Testing
- `tests/dashboard/test_api.py` — Test all API endpoints with mocked data
- `tests/dashboard/test_data.py` — Test data layer with fake stores

## Default-Disabled
- Dashboard server only starts when `vibe dashboard` is explicitly run
- No background processes, no impact on normal operation

## Implementation Order
1. Data access layer (`data.py`) + tests
2. FastAPI endpoints (`api.py`) + tests
3. React frontend (static files)
4. CLI integration (`vibe dashboard` command)
5. Full integration test

## Review Notes (Gemini CLI)
- Added SSE streaming endpoint for live updates
- Added `/api/config` for configuration visibility
- Bind to 127.0.0.1 only, strict CORS
- Use Vite + Recharts + D3.js (encapsulated)
- Handle sync/async mismatch with run_in_threadpool
- Frontend testing with Jest/Vitest (future)

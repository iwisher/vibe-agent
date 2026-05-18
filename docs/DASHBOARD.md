# Vibe Agent Dashboard

Real-time monitoring and observability UI for the Vibe Agent system.

---

## Quick Start

```bash
# Start with auth token (default)
vibe dashboard start --port 8080

# Start without auth (dev mode)
vibe dashboard start --port 8080 --no-auth

# Then open http://localhost:8080 in your browser
```

---

## Layout

```
+-------------------------------------------------------------+
|  Vibe Agent Dashboard                    v0.3.5    [Live]  |
+-------------------------------------------------------------+
|  +---------+  +---------+  +---------+  +---------+        |
|  |Sessions |  |Wiki     |  |Skills   |  |Errors   |        |
|  |   4     |  |   3     |  |   1     |  |   1     |        |
|  | +12%    |  | +5%     |  |  0%     |  |  -8%    |        |
|  +---------+  +---------+  +---------+  +---------+        |
+--------------------------+----------------------------------+
|  Recent Sessions         |  Wiki Knowledge Graph           |
|  +--------------------+  |  +---------------------------+  |
|  | sess_001...  5m30s |  | |      o---o                 |  |
|  | kimi-k2.5  [Success]|  | |     /     \                |  |
|  +--------------------+  | |    o-------o                |  |
|  | sess_002...  2m15s |  | |                             |  |
|  | gemini-2.5 [Success]|  | |  (force-directed D3)      |  |
|  +--------------------+  | |                             |  |
|  | sess_003...  8m45s |  +-------------------------------+  |
|  | claude-4   [Failed] |  |  Telemetry (24h)              |
|  +--------------------+  |  +---------------------------+  |
|  | sess_004...  1m30s |  | |  ████                     |  |
|  | qwen3-max  [Success]|  | |  ████ ██                  |  |
|  +--------------------+  | |  ████ ██ █                |  |
|                          | |  Sessions|Compactions|... |  |
|                          |  +---------------------------+  |
|                          |  |  System Info                |
|                          |  |  +-----------------------+  |
|                          |  | | Dashboard Status Online|  |
|                          |  | | Network Binding 127.0.0|  |
|                          |  | | CORS Policy Same-origin|  |
|                          |  | | API Mode     Read-only |  |
|                          |  | | Auto Refresh Every 5s  |  |
|                          |  | | Version        v0.3.5  |  |
|                          |  |  +-----------------------+  |
+--------------------------+----------------------------------+
```

---

## Architecture

```
Browser
  |
  |-- GET /                    --> index.html (React SPA shell)
  |-- GET /static/style.css    --> Dark theme CSS
  |-- GET /static/app.js       --> React components (no build step)
  |
  |-- CDN: react@18            --> React UMD
  |-- CDN: react-dom@18        --> ReactDOM UMD
  |-- CDN: react-is@18         --> ReactIs UMD (Recharts peer dep)
  |-- CDN: recharts            --> Recharts UMD (charts)
  |-- CDN: d3@7                --> D3 UMD (force graph)
  |
  |-- GET /api/stats           --> Aggregate counts
  |-- GET /api/sessions        --> Session list from SQLite
  |-- GET /api/wiki            --> Wiki pages from disk
  |-- GET /api/skills          --> Skills from disk
  |-- GET /api/telemetry       --> Metrics from SQLite
  |-- GET /api/config          --> Version, auth status
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 (CDN, no build) |
| Styling | Custom CSS (design tokens) |
| Charts | Recharts (BarChart) |
| Graph | D3.js (force simulation) |
| Backend | FastAPI + Uvicorn |
| Data | SQLite (.vibe/sessions.db, .vibe/telemetry.db) |
| Files | Markdown (wiki/*.md), YAML (skills/*/SKILL.md) |

---

## API Endpoints

| Endpoint | Method | Description | Response |
|----------|--------|-------------|----------|
| `/` | GET | Serve index.html | HTML |
| `/static/*` | GET | Static assets (CSS, JS) | File |
| `/api/stats` | GET | Aggregate statistics | `{total_sessions, total_wiki_pages, total_skills, recent_errors}` |
| `/api/sessions` | GET | All sessions | `[{session_id, state, model, iteration, message_count, duration_seconds}]` |
| `/api/wiki` | GET | All wiki pages | `[{slug, title, tags, verification_status, word_count}]` |
| `/api/skills` | GET | All skills | `[{name, version, description, install_path}]` |
| `/api/telemetry` | GET | Recent metrics | `{metrics: [{metric_name, value, timestamp, session_id}]}` |
| `/api/config` | GET | Dashboard config | `{version, auth_enabled}` |

---

## Data Sources

### Sessions (`.vibe/sessions.db`)

```sql
CREATE TABLE session_checkpoints (
    session_id TEXT PRIMARY KEY,
    state TEXT,           -- 'COMPLETED' | 'ERROR' | 'IN_PROGRESS'
    model TEXT,
    iteration INTEGER,
    created_at TEXT,
    updated_at TEXT,
    messages_json TEXT,
    plan_result_json TEXT
);
```

### Telemetry (`.vibe/telemetry.db`)

```sql
CREATE TABLE telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT,     -- 'session', 'tool_call', 'compaction', 'error', 'skill_generated'
    value REAL,
    timestamp TEXT,
    session_id TEXT
);
```

### Wiki Pages (`wiki/*.md`)

Frontmatter format:
```markdown
---
title: "Page Title"
tags: [tag1, tag2]
status: verified | draft | deprecated
---

# Content
```

### Skills (`skills/*/SKILL.md`)

Frontmatter format:
```markdown
---
name: "Skill Name"
version: "1.0.0"
description: "What this skill does"
---
```

---

## Design Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `--bg-base` | `#0a0e1a` | Page background |
| `--bg-elevated` | `#111827` | Card backgrounds |
| `--accent-blue` | `#3b82f6` | Primary, charts |
| `--accent-green` | `#10b981` | Success states |
| `--accent-red` | `#ef4444` | Errors |
| `--accent-purple` | `#8b5cf6` | Wiki/skills |
| `--text-primary` | `#f0f4f8` | Headings |
| `--text-secondary` | `#94a3b8` | Labels |

---

## Troubleshooting

### Blank page

1. Check browser console (F12) for JS errors
2. Verify all CDN scripts load in Network tab:
   - React, ReactDOM, ReactIs, Recharts, D3
3. Check `/api/stats` returns data:
   ```bash
   curl http://127.0.0.1:8080/api/stats
   ```

### "Recharts is not defined"

Recharts 3.x requires ReactIs as a peer dependency. Ensure loaded in order:
```html
<script src="react.production.min.js"></script>
<script src="react-dom.production.min.js"></script>
<script src="react-is.production.min.js"></script>  <!-- Required! -->
<script src="Recharts.min.js"></script>
```

### Empty panels

The dashboard needs sample data. Create it with:
```bash
# Sessions and telemetry are auto-created on first use
# Wiki pages: create markdown files in wiki/ directory
# Skills: create SKILL.md files in skills/ directory
```

---

## Development

### File Structure

```
vibe/dashboard/
├── server.py          # FastAPI backend
├── data.py            # SQLite queries
├── static/
│   ├── index.html     # SPA shell (CDN imports)
│   ├── style.css      # Design system
│   └── app.js         # React components
```

### Adding a New Panel

1. Create React component in `app.js`:
```javascript
function MyPanel() {
  const [data, setData] = useState([]);
  useEffect(() => {
    api.get('/api/my-endpoint').then(setData);
  }, []);
  return React.createElement('div', null, 'My Panel');
}
```

2. Add to App component:
```javascript
React.createElement('div', { className: 'panel' },
  React.createElement('div', { className: 'panel-header' },
    React.createElement('div', { className: 'panel-title' }, 'My Panel')
  ),
  React.createElement('div', { className: 'panel-body' },
    React.createElement(MyPanel, null)
  )
)
```

3. Add API endpoint in `server.py`:
```python
@app.get("/api/my-endpoint")
async def my_endpoint() -> list[dict]:
    return [{"key": "value"}]
```

---

## Security

- **Auth token**: Set `DASHBOARD_TOKEN` env var or pass `--token`
- **CORS**: Same-origin only by default
- **API mode**: Read-only (no mutations via dashboard)
- **Static files**: Served from `vibe/dashboard/static/` only

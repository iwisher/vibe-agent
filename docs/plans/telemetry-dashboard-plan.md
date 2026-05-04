# Telemetry Dashboard — Phase 1a

## Overview

Build an interactive telemetry dashboard at `/telemetry` that visualizes data from the `_telemetry` table in `memory.db`. The dashboard will display charts, tables, and filters to help developers and operators monitor system behavior.

## Current State

- `TelemetryCollector` records three event types: `compaction`, `session`, `wiki_op`
- `SharedMemoryDB.query_telemetry_summary(days)` returns aggregate stats (total events, large content count, percentage)
- Data is stored in `_telemetry` table with columns: `id`, `event_type`, `recorded_at`, `session_id`, `data` (JSON)
- No existing dashboard UI

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    /telemetry route                      │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Summary    │  │  Line Chart  │  │  Event Table  │  │
│  │  Cards      │  │  (Events/hr) │  │  (Paginated)  │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  Pie Chart  │  │  Bar Chart   │  │  Date Picker  │  │
│  │ (By type)   │  │ (By strategy)│  │  + Auto-Refresh│  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Files to Create/Modify

### New Files

1. **`vibe/memory/telemetry_queries.py`** — SQL query helpers for dashboard
2. **`vibe/web/templates/telemetry.html`** — Dashboard HTML template
3. **`tests/unit/test_telemetry_dashboard.py`** — Dashboard tests

### Modified Files

4. **`vibe/web/routes.py`** — Add `/telemetry` route
5. **`vibe/memory/shared_db.py`** — Add query methods for dashboard charts

## Detailed Plan

### Step 1: Add Query Methods to SharedMemoryDB

Add these methods to `SharedMemoryDB`:

```python
def query_telemetry_timeseries(
    self, event_type: str | None, days: int, interval: str = "hour"
) -> list[dict]:
    """Return time-series data for line chart.
    
    Returns: [{"timestamp": "2024-01-15T14:00", "count": 5}, ...]
    """

def query_telemetry_by_type(self, days: int) -> list[dict]:
    """Return event counts grouped by event_type for pie chart.
    
    Returns: [{"event_type": "compaction", "count": 42}, ...]
    """

def query_telemetry_by_strategy(self, days: int) -> list[dict]:
    """Return compaction counts grouped by strategy for bar chart.
    
    Returns: [{"strategy": "truncate", "count": 30}, ...]
    """

def query_telemetry_events(
    self, event_type: str | None, session_id: str | None, 
    days: int, offset: int, limit: int
) -> tuple[list[dict], int]:
    """Return paginated raw events with total count.
    
    Returns: (events_list, total_count)
    """

def query_wiki_op_summary(self, days: int) -> dict:
    """Return wiki operation metrics.
    
    Returns: {"total_ops": 150, "avg_duration_ms": 45.2, "by_type": {...}}
    """
```

### Step 2: Create telemetry_queries.py (Optional Helper Module)

If queries get complex, extract SQL building into a helper:

```python
def build_timeseries_sql(
    event_type: str | None, days: int, interval: str
) -> tuple[str, list]:
    """Build SQL for time-series aggregation."""
    ...
```

### Step 3: Add Route in routes.py

```python
@app.get("/telemetry")
async def telemetry_dashboard(request: Request) -> HTMLResponse:
    """Serve the telemetry dashboard page."""
    return templates.TemplateResponse("telemetry.html", {"request": request})

@app.get("/api/telemetry/summary")
async def api_telemetry_summary(days: int = 30) -> dict:
    """Return summary stats."""
    ...

@app.get("/api/telemetry/timeseries")
async def api_telemetry_timeseries(
    event_type: str | None = None,
    days: int = 7,
    interval: str = "hour",
) -> list[dict]:
    """Return time-series data for line chart."""
    ...

@app.get("/api/telemetry/by-type")
async def api_telemetry_by_type(days: int = 30) -> list[dict]:
    """Return events grouped by type for pie chart."""
    ...

@app.get("/api/telemetry/by-strategy")
async def api_telemetry_by_strategy(days: int = 30) -> list[dict]:
    """Return compactions grouped by strategy for bar chart."""
    ...

@app.get("/api/telemetry/events")
async def api_telemetry_events(
    event_type: str | None = None,
    session_id: str | None = None,
    days: int = 7,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """Return paginated raw events."""
    ...

@app.get("/api/telemetry/wiki-summary")
async def api_telemetry_wiki_summary(days: int = 30) -> dict:
    """Return wiki operation summary."""
    ...
```

### Step 4: Create telemetry.html Template

The template will use:
- **Chart.js** (CDN) for all charts — lightweight, no build step needed
- **Tailwind CSS** (already available) for layout
- Vanilla JS for interactivity

Key UI sections:

```
┌──────────────────────────────────────────────────────────┐
│  Telemetry Dashboard                    [7d] [30d] [90d] │
│  Auto-refresh: ● 30s  ○ 60s  ○ Off                      │
├──────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ 1,247    │ │ 89       │ │ 23%      │ │ 45ms     │   │
│  │ Total    │ │ Sessions │ │ Large    │ │ Avg Wiki │   │
│  │ Events   │ │ Active   │ │ Content  │ │ Latency  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
├──────────────────────────────────────────────────────────┤
│  Events Over Time (hourly)                               │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Line Chart: compaction / session / wiki_op      │   │
│  │                                                  │   │
│  └──────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────┤
│  Events by Type          │  Compaction Strategy          │
│  ┌───────────────────┐   │  ┌───────────────────────┐   │
│  │  Pie Chart        │   │  │  Bar Chart            │   │
│  │  compaction 60%   │   │  │  truncate    ████████ │   │
│  │  session    25%   │   │  │  summarize   ████     │   │
│  │  wiki_op    15%   │   │  │  none        ██       │   │
│  └───────────────────┘   │  └───────────────────────┘   │
├──────────────────────────────────────────────────────────┤
│  Recent Events                                            │
│  ┌────────────────────────────────────────────────────┐  │
│  │ Type      │ Session │ Time      │ Data             │  │
│  │ compaction│ abc123  │ 14:02:31  │ size=45K tokens=12K│
│  │ session   │ def456  │ 14:01:15  │ duration=32s     │  │
│  └────────────────────────────────────────────────────┘  │
│  [◀ Prev]  [1] [2] [3] [4] [Next ▶]                      │
└──────────────────────────────────────────────────────────┘
```

### Step 5: JavaScript Logic (inline in template)

```javascript
// State
let currentDays = 7;
let refreshInterval = 30000; // 30s
let refreshTimer = null;

// Fetch functions
async function loadSummary() { ... }
async function loadTimeseries() { ... }
async function loadByType() { ... }
async function loadByStrategy() { ... }
async function loadEvents(page = 0) { ... }
async function loadWikiSummary() { ... }

// Chart initialization
let timeseriesChart = null;
let pieChart = null;
let barChart = null;

// Auto-refresh
function startRefresh() { ... }
function stopRefresh() { ... }

// Event listeners for date range buttons, page size, auto-refresh toggle
```

### Step 6: Tests

```python
# tests/unit/test_telemetry_dashboard.py

class TestTelemetryQueries:
    def test_timeseries_query(self): ...
    def test_by_type_query(self): ...
    def test_by_strategy_query(self): ...
    def test_events_pagination(self): ...
    def test_wiki_summary(self): ...
    def test_empty_db(self): ...
    def test_date_filter(self): ...

class TestTelemetryRoutes:
    def test_dashboard_page_loads(self): ...
    def test_summary_api(self): ...
    def test_timeseries_api(self): ...
    def test_by_type_api(self): ...
    def test_by_strategy_api(self): ...
    def test_events_api_pagination(self): ...
    def test_events_api_filtering(self): ...
    def test_invalid_params(self): ...
```

## Implementation Order

1. **`shared_db.py`** — Add 5 query methods (most critical, unblocks everything)
2. **`routes.py`** — Add routes (connects DB to HTTP)
3. **`telemetry.html`** — Build the UI (consumes the API)
4. **`test_telemetry_dashboard.py`** — Tests (TDD or post-implementation)

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Chart library | Chart.js (CDN) | Lightweight, no build step, already well-known |
| Date range | 7d / 30d / 90d buttons | Common ranges, simple UX |
| Auto-refresh | 30s / 60s / Off toggle | Real-time monitoring without polling too hard |
| Pagination | Server-side (offset/limit) | Efficient for large datasets |
| Time-series interval | Auto-scale by days | Hourly for ≤7d, daily for >7d |
| Error handling | Graceful degradation | If a query fails, show "N/A" not a broken page |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| FTS5 not available | Low | Low | Already handled in shared_db.py |
| Large `_telemetry` table | Medium | Medium | Paginated queries, date filtering |
| Chart.js CDN unavailable | Low | High | Inline fallback message |
| JSON parsing errors | Low | Low | Try/except in query methods |

## Acceptance Criteria

- [ ] `/telemetry` page loads with summary cards, 3 charts, and event table
- [ ] Date range filter (7d / 30d / 90d) updates all charts
- [ ] Auto-refresh works at 30s and 60s intervals
- [ ] Event table is paginated (50 per page)
- [ ] Event type filter works on the table
- [ ] All API endpoints return valid JSON
- [ ] Tests pass for all new query methods and routes
- [ ] Dashboard works when `_telemetry` table is empty

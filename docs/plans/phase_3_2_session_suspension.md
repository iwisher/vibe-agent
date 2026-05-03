# Phase 3.2: Durable Session Suspension & Resumption — Implementation Plan

## Overview

Enable Vibe Agent to save in-flight session state to SQLite on every state transition, and resume incomplete sessions on startup. This makes long-running agentic tasks durable against process crashes, restarts, and interruptions.

**Scope**: Serialization, resumption, and CLI commands. All 3 sub-tasks in this session.

---

## Design Decisions

### 1. Serialization Target: New `session_checkpoints` table in existing `traces.db`

The existing `TraceStore` has `sessions`, `messages`, `tool_calls` tables, but they store **completed** session history. We need a new table for **in-flight** checkpoint state:

```sql
CREATE TABLE IF NOT EXISTS session_checkpoints (
    session_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,           -- QueryState name
    messages_json TEXT NOT NULL,   -- JSON array of Message dicts
    plan_result_json TEXT,         -- JSON of PlanResult (nullable)
    iteration INTEGER DEFAULT 0,
    feedback_retries INTEGER DEFAULT 0,
    model TEXT,
    created_at TEXT,
    updated_at TEXT
);
```

Why not reuse the `sessions`/`messages` tables?
- `sessions` has `success` (bool) and `end_time` — in-flight sessions don't have these
- `messages` stores redacted content for completed sessions; checkpoints need full unredacted state for resumption
- Separation of concerns: completed session log vs. in-flight checkpoint

### 2. Resume Granularity: `vibe sessions` lists all, `vibe resume` auto-resumes latest incomplete

- `vibe sessions` — list all sessions with status (running/completed/error)
- `vibe resume` — resume the most recent incomplete session (by `updated_at DESC`)
- `vibe resume <session_id>` — resume a specific session

### 3. Message Serialization: JSON blob in `messages_json` column

`QueryLoop.messages` is a list of `Message` dataclass instances. We serialize to JSON array of dicts:

```python
[
    {
        "role": "user",
        "content": "...",
        "tool_calls": [...],      # optional
        "tool_call_id": "...",    # optional
        "model_version": "..."    # optional
    }
]
```

Why not normalized rows?
- Messages are immutable once checkpointed; no need for CRUD on individual messages
- Single INSERT/UPDATE per checkpoint is faster than N message rows
- Simpler schema, easier to reason about
- JSON blob size is bounded (context window limit ~8000 tokens ≈ ~32KB text)

### 4. Tool State During Suspension: Wait for completion

If the agent is mid-tool-execution (e.g., a bash command running), we checkpoint **after** the tool completes. The checkpoint is written at the end of each state transition (PLANNING → PROCESSING → TOOL_EXECUTION → SYNTHESIZING, etc.), never mid-tool.

This is the practical choice:
- Capturing partial tool state (subprocess handles, temp files) is extremely hard
- Most tool calls are fast (<120s timeout)
- If the process dies mid-tool, the tool result is lost and the session resumes from the last completed state

### 5. Session Lifecycle

```
User starts vibe → QueryLoop.run() begins
    ↓
Session starts → write checkpoint (state=PLANNING, messages=[user_query])
    ↓
Each state transition → update checkpoint (state, messages, iteration)
    ↓
Session completes (COMPLETED/ERROR/STOPPED/INCOMPLETE)
    → delete checkpoint (cleanup)
    → log to TraceStore (existing behavior, unchanged)
    ↓
Next startup → QueryLoopFactory checks for checkpoints
    → If found, offer resume or start fresh
```

---

## Files to Modify

| File | Change |
|------|--------|
| `vibe/harness/memory/session_store.py` | **NEW** — `SessionStore` class with SQLite CRUD for checkpoints |
| `vibe/harness/memory/__init__.py` | Export `SessionStore` |
| `vibe/core/query_loop.py` | Add `_checkpoint()` method; call at state transitions; add `resume()` classmethod |
| `vibe/core/query_loop_factory.py` | Add `session_store` parameter; check for incomplete sessions on startup |
| `vibe/cli/main.py` | Add `vibe resume` and `vibe sessions` commands |
| `tests/test_session_store.py` | **NEW** — Unit tests for SessionStore |
| `tests/test_query_loop_resume.py` | **NEW** — Integration tests for resume flow |

---

## Implementation Steps

### Step 1: SessionStore (`vibe/harness/memory/session_store.py`)

```python
class SessionStore:
    """SQLite-backed store for in-flight session checkpoints.
    
    Separate from TraceStore — checkpoints are for resumption,
    traces are for completed session history.
    """
    
    def __init__(self, db_path: str | None = None):
        # Same default path as TraceStore: ~/.vibe/memory/traces.db
        pass
    
    def _init_db(self) -> None:
        # CREATE TABLE IF NOT EXISTS session_checkpoints
        pass
    
    def save_checkpoint(self, session_id: str, state: str, messages: list[dict],
                       plan_result: dict | None = None, iteration: int = 0,
                       feedback_retries: int = 0, model: str | None = None) -> None:
        # INSERT OR REPLACE
        pass
    
    def load_checkpoint(self, session_id: str) -> dict | None:
        # SELECT → dict or None
        pass
    
    def list_incomplete(self, limit: int = 20) -> list[dict]:
        # SELECT * FROM session_checkpoints ORDER BY updated_at DESC
        pass
    
    def delete_checkpoint(self, session_id: str) -> None:
        # DELETE FROM session_checkpoints WHERE session_id = ?
        pass
    
    def has_checkpoint(self, session_id: str) -> bool:
        # EXISTS check
        pass
```

### Step 2: QueryLoop Integration

Add to `QueryLoop`:

```python
# In __init__:
self._session_store: SessionStore | None = None  # set by factory

# New method:
def _checkpoint(self) -> None:
    """Serialize current state to SessionStore."""
    if self._session_store is None or self._session_id is None:
        return
    messages_json = [
        {
            "role": m.role,
            "content": m.content,
            "tool_calls": m.tool_calls,
            "tool_call_id": m.tool_call_id,
            "model_version": m.model_version,
        }
        for m in self.messages
    ]
    plan_json = None
    if self._plan_result is not None:
        plan_json = {
            "selected_tool_names": self._plan_result.selected_tool_names,
            "system_prompt_append": self._plan_result.system_prompt_append,
        }
    self._session_store.save_checkpoint(
        session_id=self._session_id,
        state=self._state.name,
        messages=messages_json,
        plan_result=plan_json,
        iteration=getattr(self, '_iteration', 0),  # track iteration
        feedback_retries=self._feedback_retries,
        model=self.llm.model if self.llm else None,
    )

# Call _checkpoint() at the end of _set_state():
def _set_state(self, state: QueryState) -> None:
    self._state = state
    self._checkpoint()  # NEW

# New classmethod for resume:
@classmethod
async def resume(cls, session_id: str, session_store: SessionStore,
                 factory: QueryLoopFactory) -> "QueryLoop":
    """Restore a QueryLoop from a checkpoint."""
    checkpoint = session_store.load_checkpoint(session_id)
    if checkpoint is None:
        raise ValueError(f"No checkpoint found for session {session_id}")
    
    # Create fresh QueryLoop via factory
    loop = factory.create()
    loop._session_store = session_store
    loop._session_id = session_id
    loop._state = QueryState[checkpoint["state"]]
    loop.messages = [
        Message(**m) for m in checkpoint["messages"]
    ]
    loop._feedback_retries = checkpoint.get("feedback_retries", 0)
    if checkpoint.get("plan_result"):
        loop._plan_result = PlanResult(**checkpoint["plan_result"])
    # iteration is restored but run() will re-count from 0
    return loop
```

### Step 3: QueryLoopFactory Integration

Add to `QueryLoopFactory.create()`:

```python
# Create SessionStore (shares same DB as TraceStore)
session_store = self._create_session_store()
if session_store is not None:
    kwargs["session_store"] = session_store
```

Add `_create_session_store()` method (similar pattern to `_create_trace_store()`).

### Step 4: CLI Commands

In `vibe/cli/main.py`:

```python
session_app = typer.Typer(help="Session management")
app.add_typer(session_app, name="session")

@session_app.command("list")
def session_list(limit: int = typer.Option(20, "--limit", "-n")):
    """List incomplete sessions that can be resumed."""
    store = SessionStore()
    sessions = store.list_incomplete(limit=limit)
    # ... table output

@session_app.command("resume")
def session_resume(
    session_id: str | None = typer.Argument(None, help="Session ID to resume (default: latest)"),
):
    """Resume an incomplete session."""
    store = SessionStore()
    if session_id is None:
        sessions = store.list_incomplete(limit=1)
        if not sessions:
            console.print("[yellow]No incomplete sessions found.[/yellow]")
            raise typer.Exit(code=0)
        session_id = sessions[0]["session_id"]
    
    # Create factory and resume
    factory = QueryLoopFactory(...)
    loop = asyncio.run(QueryLoop.resume(session_id, store, factory))
    
    # Continue interactive mode with resumed loop
    asyncio.run(interactive_mode(loop))
```

### Step 5: Cleanup on Session Completion

In `QueryLoop.run()` finally block, after logging to trace_store:

```python
# Delete checkpoint on completion (session is now durable in trace_store)
if self._session_store and self._session_id:
    self._session_store.delete_checkpoint(self._session_id)
```

---

## Testing Plan

### Unit Tests (`tests/test_session_store.py`)

1. `test_save_and_load_checkpoint` — roundtrip serialization
2. `test_list_incomplete` — ordering by updated_at
3. `test_delete_checkpoint` — removal after completion
4. `test_messages_json_roundtrip` — Message dataclass fidelity
5. `test_plan_result_roundtrip` — PlanResult serialization
6. `test_concurrent_updates` — rapid checkpoint updates don't corrupt

### Integration Tests (`tests/test_query_loop_resume.py`)

1. `test_checkpoint_written_on_state_transition` — verify checkpoint exists after run()
2. `test_checkpoint_deleted_on_completion` — verify cleanup
3. `test_resume_restores_messages` — resume and verify message list
4. `test_resume_restores_state` — verify QueryState after resume
5. `test_resume_continues_conversation` — resume + add new message → continues

### Regression Tests

- Full test suite: `pytest tests/ -q` — must maintain 948 passing
- Verify TraceStore behavior unchanged (completed sessions still log correctly)

---

## Security Considerations

1. **Secret redaction**: SessionStore must redact secrets before persisting messages. Reuse `BaseTraceStore._redact()` logic.
2. **No pickle**: JSON only, no pickle serialization.
3. **SQLite injection**: Use parameterized queries exclusively.

---

## Rollback Plan

If issues arise:
1. `session_checkpoints` table is additive — dropping it leaves all other tables intact
2. QueryLoop falls back to no-checkpoint behavior if `session_store` is None
3. CLI commands are additive — removing them doesn't break existing flows

---

## Deliverables

- [ ] `vibe/harness/memory/session_store.py` — SessionStore implementation
- [ ] `vibe/harness/memory/__init__.py` — Export SessionStore
- [ ] `vibe/core/query_loop.py` — Checkpoint integration + resume()
- [ ] `vibe/core/query_loop_factory.py` — Factory wiring
- [ ] `vibe/cli/main.py` — `vibe session list` and `vibe session resume` commands
- [ ] `tests/test_session_store.py` — Unit tests
- [ ] `tests/test_query_loop_resume.py` — Integration tests
- [ ] Full test suite passes (948+ tests)
- [ ] Gemini CLI code review passes

---

*Plan written: 2026-05-02 | Phase: 3.2 | Estimated effort: 1 session*

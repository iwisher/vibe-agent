# Dynamic Status Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide intermediate progress updates during the agent's query loop and a `/verbose` toggle in the CLI.

**Architecture:** Extend `QueryResult` to support status messages, update `QueryLoop` to yield these messages during key transitions, and update the CLI to use `rich.status.Status` for dynamic display.

**Tech Stack:** Python, Rich

---

### Task 1: Update `QueryResult` Dataclass

**Files:**
- Modify: `vibe/core/query_loop.py`
- Test: `tests/test_query_loop.py`

- [ ] **Step 1: Write the failing test**

```python
def test_query_result_status_fields():
    from vibe.core.query_loop import QueryResult
    qr = QueryResult(is_status=True, status_message="Testing...")
    assert qr.is_status is True
    assert qr.status_message == "Testing..."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_loop.py -k test_query_result_status_fields`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'is_status'`

- [ ] **Step 3: Update `QueryResult` dataclass**

```python
@dataclass
class QueryResult:
    response: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
    error: Exception | None = None
    context_truncated: bool = False
    metrics: Metrics | None = None
    state: QueryState = QueryState.IDLE
    is_status: bool = False
    status_message: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_query_loop.py -k test_query_result_status_fields`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/core/query_loop.py
git commit -m "feat: add status fields to QueryResult"
```

---

### Task 2: Yield Status Updates in `QueryLoop`

**Files:**
- Modify: `vibe/core/query_loop.py`
- Test: `tests/test_query_loop.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_query_loop_yields_status(mock_llm, tool_system):
    from vibe.core.query_loop import QueryLoop
    loop = QueryLoop(llm_client=mock_llm, tool_system=tool_system)
    
    results = []
    async for res in loop.run("test query"):
        results.append(res)
    
    status_updates = [r for r in results if r.is_status]
    assert len(status_updates) > 0
    assert any("Planning" in r.status_message for r in status_updates)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_query_loop.py -k test_query_loop_yields_status`
Expected: FAIL (assertion error, no status updates found)

- [ ] **Step 3: Add `yield` statements to `QueryLoop.run()`**

In `vibe/core/query_loop.py`, add yields at:
- Start of `run()`: `yield QueryResult(is_status=True, status_message="Planning strategy...", state=self._state)`
- Before `llm.complete()`: `yield QueryResult(is_status=True, status_message=f"Waiting for {self.llm.model}...", state=self._state)`
- Before `tool_executor.execute()`: `yield QueryResult(is_status=True, status_message=f"Executing tools: {tool_names}...", state=self._state)`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_query_loop.py -k test_query_loop_yields_status`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vibe/core/query_loop.py
git commit -m "feat: yield status updates in QueryLoop"
```

---

### Task 3: Implement Dynamic Status and `/verbose` in CLI

**Files:**
- Modify: `vibe/cli/main.py`
- Test: Manual verification (or mock CLI test)

- [ ] **Step 1: Add `/verbose` toggle logic to `interactive_mode`**

```python
# Initialize verbose_mode at the start of interactive_mode
verbose_mode = False

# Inside while loop
if user_input.lower() == "/verbose":
    verbose_mode = not verbose_mode
    console.print(f"Verbose mode {'enabled' if verbose_mode else 'disabled'}.")
    continue
```

- [ ] **Step 2: Update `interactive_mode` to use `rich.status.Status`**

```python
with console.status("[dim]Thinking...[/dim]", spinner="dots") as status_spinner:
    async for result in query_loop.run():
        if result.is_status:
            if verbose_mode:
                console.print(f"[dim]  → {result.status_message}[/dim]")
            else:
                status_spinner.update(f"[dim]{result.status_message}[/dim]")
            continue
        
        # Original logic for printing results...
```

- [ ] **Step 3: Update `single_query_mode` similarly (without toggle)**

- [ ] **Step 4: Commit**

```bash
git add vibe/cli/main.py
git commit -m "feat: implement dynamic status display and /verbose toggle in CLI"
```

---

### Task 4: Final Validation

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/`

- [ ] **Step 2: Manual Smoke Test**
1. Start Vibe Agent: `python -m vibe`
2. Run a query: `How are you?` (Observe spinner)
3. Enable verbose: `/verbose`
4. Run another query: `13 * 7` (Observe log lines)
5. Disable verbose: `/verbose`
6. Run another query: `write "hello" to test.txt` (Observe spinner updating for tool)

- [ ] **Step 3: Commit final changes**

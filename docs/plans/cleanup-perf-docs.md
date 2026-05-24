# Plan: Code Cleanup + Performance + Documentation

## Task 1: Code Cleanup (cleanup)

Fix all ruff lint issues in `vibe/`:
- F401: Unused imports (dataclasses.field in cost_tracker.py, latency_tracker.py)
- F821: Undefined name LLMResponse in adapters/base.py
- F841: Unused local variables (shadow_created in coordinators.py, query_loop in cli/main.py)
- I001: Unsorted import blocks in cli/main.py, core/query_loop.py
- W293: Blank lines with whitespace in core/coordinators.py
- E501: Line too long (selective — only fix the worst offenders, not all)

Run `ruff check vibe/` after to verify clean.
Run `pytest tests/ -x -q` to verify no regressions.

## Task 2: Performance Optimization (perf)

The slowest test by far: `tests/test_skill_installer.py::test_install_git_clone_timeout` at 60s.
It tries to connect to `http://192.0.2.1/nonexistent.git` and waits for TCP timeout.

Options:
a) Mock the git clone subprocess to avoid real network timeout
b) Use a localhost binding that immediately rejects (faster fail)
c) Mark with `@pytest.mark.slow` and skip in fast runs

Best: Mock `subprocess.run` or `asyncio.create_subprocess_exec` to return a timeout error immediately.

Also check other slow tests (>2s) for similar issues:
- tests/test_model_gateway.py::test_complete_timeout (6.64s)
- tests/test_model_gateway.py::test_complete_rate_limit (6.36s)
- tests/test_model_gateway.py::test_complete_auth_error (5.49s)

These likely have real sleep/delay. Mock the delays.

Run `pytest tests/ --durations=10` before and after to verify improvement.

## Task 3: Documentation Update (docs)

Update `docs/ROADMAP.md`:
- Line 323: Change "1420+ tests collected, 1420+ passing" to "1395 tests collected, 1395 passing"
- Verify Phase 4.3 (Multi-Agent Swarm) — code exists in vibe/swarm/, tests pass. Mark as completed.
- Verify all "ALL CLOSED in v0.3.3" claims match actual code state.
- Update "Last updated" date.

Run no tests needed (docs only), but verify file renders correctly.

## Review Gates

Each task:
1. Implementer sub-agent does the work
2. AGY code review (background)
3. Clean sub-agent review and approval
4. Only then mark complete

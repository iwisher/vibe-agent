# Vibe Agent — Validation Report

## Random Agent Idea: Documentation Drift Agent

### Idea
An agent that:
1. Uses `bash` to find all `.py` files in a repo
2. Uses `read_file` to inspect each file
3. Uses the LLM to compare function signatures against docstrings
4. Uses `write_file` to generate a `drift_report.md`

### Gemini Code Skill Test

**Prompt:** Asked Gemini CLI to write `docs/drift_agent.py` as a standalone demo using the Vibe Agent platform APIs.

**Result:** Gemini produced a 58-line script but with **multiple API mismatches**:
- `LLMClient(model_name="gpt-4o")` — our constructor is `LLMClient(base_url, model, api_key)`
- `ToolSystem(tools=tools)` — ours uses `register_tool()`, no `tools` kwarg
- `QueryLoop(llm=llm, system_prompt=...)` — ours uses `llm_client`, no `system_prompt`
- `final_state = await agent.run(task_prompt)` — `run()` returns an async generator, not a final state object

**Fix:** Manually rewrote the script to match the actual Vibe Agent APIs.

### Lesson
Even a capable coding agent (Gemini) hallucinates APIs when it cannot read the full implementation or when the harness surface area is new. This validates the design decision to:
1. Keep the API surface small and explicit
2. Use evals to catch integration mismatches
3. Treat the harness (tool schemas, constructor signatures, loop behavior) as a strict contract

### Fixed Script Location
`/Users/rsong/DevSpace/vibe-agent/docs/drift_agent.py`

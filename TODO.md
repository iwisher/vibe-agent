# Vibe Agent — TODO

> Active task tracking for the development team.

---

## Open Tasks

### High Priority

- [ ] **Add `vibe/harness/instructions.py`**
  - Load `~/.vibe/AGENTS.md` and `./AGENTS.md` into system prompt
  - Parse `~/.vibe/skills/*.md` with YAML frontmatter
  - Support progressive disclosure (`auto_load: true` only by default)

- [ ] **Add `vibe/harness/feedback.py`**
  - Implement `FeedbackEngine` with self-verifier + independent evaluator
  - Define `FeedbackResult` dataclass (`score`, `issues`, `suggested_fix`)
  - Wire feedback loop into QueryLoop for auto-retry when score < threshold

- [ ] **Expand eval suite to 10 cases**
  - Categories: file editing, bash math, multi-step reasoning, error recovery, tool selection
  - Target: 8/10 passing before shipping

- [ ] **Build eval runner CLI (`vibe eval run`)**
  - Command: `vibe eval run --tag file_ops`
  - Show pass/fail per eval and aggregate score
  - Record results in SQLite eval store

### Medium Priority

- [ ] **Replace naive token estimation in `ContextCompactor`**
  - Current: `chars / 4.0`
  - Better: integrate `tiktoken` or model-aware tokenizer

- [ ] **Configurable compaction strategies**
  - `SummarizeStrategy`, `OffloadStrategy`, `DropStrategy`
  - Make strategy user-configurable

- [ ] **Make `TraceStore` path configurable**
  - Currently hardcoded to `Path.home() / ".vibe" / "memory" / "traces.db"`
  - Support env var `VIBE_MEMORY_DIR`

- [ ] **Add `vibe/harness/memory/wiki.py`**
  - Minimal markdown-based wiki with Compiled Truth / Timeline split

### Low Priority / Phase 2

- [ ] **Async session orchestration**
- [ ] **React dashboard**
- [ ] **MCP bridge**
- [ ] **Multi-provider model routing**
- [ ] **Auto-harness optimizer (`vibe optimize`)**

---

## Recently Completed

- [x] Remove hardcoded API key fallbacks
- [x] Harden BashTool security (regex denylist + whitelist mode)
- [x] Integrate HookPipeline into QueryLoop
- [x] Add QueryState machine to QueryLoop
- [x] Create project tracking docs (ROADMAP, TODO, CHANGELOG)

---

*Last updated: 2026-04-15*

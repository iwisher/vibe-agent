# Vibe-Agent System Design Document

**Version:** 0.1.0  
**Last Updated:** 2026-04-18  
**Status:** Post-Phase-5 (Decoupling & Polish Complete)

---

## 1. Overview

Vibe-Agent is an **open agent harness platform** that treats the harness (not the model) as the product. It combines:

- **Hermes-style synchronous delegation** — isolated subagents with constrained tool access
- **OpenClaw-style async orchestration** — (deferred to Phase 2)
- **Eval-driven hill-climbing** — every behavior change is validated against a built-in eval suite

The design follows four principles:
1. **Port, don't rewrite** — reuse proven patterns from existing agent systems
2. **Test-driven evals** — every eval is a regression test
3. **Thin interfaces, fat harness** — CLI/API are wrappers around the harness core
4. **No hidden magic** — every behavior is inspectable and overridable

---

## 2. Key Components

### 2.1 Core Layer (`vibe/core/`)

#### `model_gateway.py` — LLM Client Gateway
- **`LLMClient`**: Unified async client for OpenAI-compatible APIs
  - **Circuit breaker**: per-model failure tracking with cooldown (default: 5 failures → 60s cooldown)
  - **Auto-fallback**: traverses fallback_chain on model-unavailability errors
  - **Retry integration**: delegates to `ErrorRecovery` for transient failures
  - **Request/response hooks**: `on_request` / `on_response` callbacks for observability
  - **`structured_output()`**: forces JSON schema compliance via system prompt + markdown cleanup
- **`LLMResponse`**: normalized response dataclass with `error`, `error_type`, `usage`, `tool_calls`
- **`CircuitBreaker`**: simple half-open breaker for resilience

#### `query_loop.py` — Main Agent Loop
- **`QueryLoop`**: the heart of the agent. Stateful async generator yielding `QueryResult`
  - **State machine**: `IDLE → PLANNING → PROCESSING → TOOL_EXECUTION → SYNTHESIZING → COMPLETED/INCOMPLETE/ERROR/STOPPED`
  - **Planning stage**: calls `ContextPlanner` before first LLM call to select tools/skills/MCPs
  - **Context compaction**: `ContextCompactor` triggers before each LLM call if token budget exceeded
  - **Feedback loop**: `FeedbackEngine` evaluates non-tool responses; auto-retries if score < threshold
  - **Hook pipeline**: PRE_VALIDATE → PRE_MODIFY → PRE_ALLOW → execute → POST_EXECUTE → POST_FIX
  - **Max iteration guard**: stops at `max_iterations` (default 50), sets `INCOMPLETE` state
  - **Resource cleanup**: `close()` shuts down LLM client and MCP bridge

#### `context_compactor.py` — Token Budget Management
- **`ContextCompactor`**: keeps conversation within `max_tokens` budget
  - **Token estimation**: prefers `tiktoken` (cl100k_base), falls back to `chars / 4.0`
  - **Strategies**:
    - `TRUNCATE`: preserve system messages + N recent messages, summarize middle as placeholder
    - `LLM_SUMMARIZE`: (optional) uses LLM to generate semantic summary of dropped messages
  - **Per-message truncation**: caps individual messages at `max_chars_per_msg`

#### `error_recovery.py` — Resilient Execution
- **`ErrorRecovery`**: generic retry wrapper with exponential backoff + jitter
- **`RetryPolicy`**: configurable max_retries, delay, backoff_factor, exception whitelist/blacklist
  - Default retryable: `httpx.HTTPStatusError`, `NetworkError`, `TimeoutException`, `ConnectionError`
  - Excluded: `CancelledError`, `KeyboardInterrupt`, `SystemExit`

#### `config.py` — Configuration Hierarchy
- **`VibeConfig`**: central configuration loaded from `~/.vibe/config.yaml` with env overrides
  - Sections: `llm`, `fallback`, `compactor`, `query_loop`, `retry`, `eval`
  - Env var prefix: `VIBE_*` (e.g., `VIBE_MODEL`, `VIBE_BASE_URL`, `VIBE_MAX_ITERATIONS`)
  - Auto-creates default config file on first load
  - Validation in `__post_init__` for all numeric bounds

#### `query_loop_factory.py` — Wiring Factory
- **`QueryLoopFactory`**: centralized factory for creating fully-wired `QueryLoop` instances
  - Creates `LLMClient` + `ToolSystem` (bash, read_file, write_file)
  - Optional: compactor, error recovery, hooks
  - `from_profile()` constructor for multi-model benchmarking

---

### 2.2 Tool Layer (`vibe/tools/`)

#### `tool_system.py` — Tool Registry
- **`Tool`**: abstract base with `get_schema()` → OpenAI function schema, `execute()` → `ToolResult`
- **`ToolSystem`**: registry that maps tool names to implementations; exposes schemas to LLM

#### `bash.py` — Sandboxed Bash Execution
- **`BashTool`**: executes commands via `asyncio.create_subprocess_exec` (NOT shell)
- **`BashSandbox`**: configurable working_dir, timeout, allowed_commands whitelist, dangerous pattern denylist
- **Three-layer defense**:
  1. **Primary**: `create_subprocess_exec` + `shlex.split` — no shell interpretation
  2. **Secondary**: reject unquoted shell metacharacters (`|&;><$\``)
  3. **Tertiary**: regex denylist for dangerous patterns (rm -rf /, sudo, curl | bash, fork bombs, etc.)
- **Timeout cleanup**: kills entire process group via `os.killpg()` to prevent orphans
- **Path redirect**: `/tmp/vibe_*` → `VIBE_EVAL_WORK_DIR` for eval isolation

#### `file.py` — File Operations
- **`ReadFileTool`**: line-oriented read with offset/limit; 10MB size cap
- **`WriteFileTool`**: write with parent dir creation; 5MB size cap
- **`_resolve_and_jail()`**: resolves symlinks and enforces `root_dir` jail via `Path.relative_to()`
- **Path redirect**: same `/tmp/vibe_*` → `VIBE_EVAL_WORK_DIR` mechanism as bash

#### `mcp_bridge.py` — MCP Server Bridge
- **`MCPBridge`**: connects to external MCP (Model Context Protocol) servers
  - Supports stdio and HTTP transports
  - Tool discovery + execution proxy
  - Connection pooling (reuses `httpx.AsyncClient` and stdio subprocess)
- **`MCPServerConfig`**: server endpoint configuration with mutable-default safety

#### `skill_manage.py` — Skill Lifecycle
- **`SkillManageTool`**: CRUD for skills under `~/.vibe/skills/`
- Path traversal protection via `relative_to()` check

#### `_utils.py` — Shared Tool Helpers
- **`extract_tool_call_name()`**: normalizes tool call name extraction from dict/object
- **`extract_tool_call_arguments()`**: normalizes argument extraction

---

### 2.3 Harness Layer (`vibe/harness/`)

#### `constraints.py` — Constraint Hook Pipeline
- **`HookPipeline`**: ordered constraint system with 5 stages
  - `PRE_VALIDATE`: validate arguments (e.g., path traversal check)
  - `PRE_MODIFY`: transform arguments (e.g., path redirect)
  - `PRE_ALLOW`: final approval gate (e.g., permission gate for destructive tools)
  - `POST_EXECUTE`: inspect results
  - `POST_FIX`: auto-fix results (e.g., truncate oversized outputs)
- **`HookOutcome`**: `(allow, reason, modified_arguments, modified_result)`
- **Built-in hooks**:
  - `permission_gate_hook()`: blocks destructive tools without `user_approved` metadata
  - `policy_hook()`: blocks dangerous bash patterns (sudo, rm -rf /, curl | bash, etc.)

#### `feedback.py` — Eval-Driven Self-Improvement
- **`FeedbackEngine`**: evaluates LLM outputs using the LLM itself (self-verification)
  - `self_verify()`: critiques output against criteria → `FeedbackResult(score, issues, suggested_fix)`
  - `independent_evaluate()`: evaluates against structured rubric
  - Uses `structured_output()` for deterministic JSON parsing
  - Fails safe: returns neutral score (0.5) on evaluation failure

#### `instructions.py` — Agent Instruction Loader
- **`InstructionLoader`**: loads `~/.vibe/AGENTS.md` and `./AGENTS.md`
- **`InstructionSet`**: builds system prompt from global + project agents + active skills
- **`Skill`**: YAML-frontmatter markdown files in `~/.vibe/skills/*.md`
  - Fields: `name`, `description`, `auto_load`, `tags`
  - Progressive disclosure: only `auto_load: true` skills included by default

#### `planner.py` — Pre-LLM Context Planner
- **`ContextPlanner`**: lightweight keyword-based planner run BEFORE the LLM call
  - Selects relevant tools, skills, and MCPs based on query keyword matching
  - Retrieves similar historical sessions from `TraceStore` for augmentation
  - Produces `PlanResult`: selected items + `system_prompt_append` with relevant context
  - **Safety fallback**: if planner filters out all tools, exposes full tool set

#### `orchestration/sync_delegate.py` — Subagent Delegation
- **`SyncDelegate`**: runs up to N isolated subagents in parallel
  - Each task gets its own `QueryLoop` instance (full isolation)
  - Semaphore-controlled concurrency (default 3 workers)
  - Per-task timeout support
  - Factory-based: can inject custom `QueryLoopFactory`, `LLMClient`, or `ToolSystem`

---

### 2.4 Memory Layer (`vibe/harness/memory/`)

#### `trace_store.py` — Session Persistence
- **`TraceStore`**: SQLite-based trace storage at `~/.vibe/memory/traces.db`
  - Tables: `sessions`, `messages`, `tool_calls`, `session_embeddings`
  - **Vector search**: optional `sentence-transformers` embeddings for semantic session retrieval
  - **Keyword fallback**: cosine-similarity on keyword overlap when embeddings unavailable

#### `eval_store.py` — Eval Case & Result Storage
- **`EvalStore`**: SQLite storage for eval cases and run results at `~/.vibe/memory/evals.db`
  - Loads eval cases from YAML files (`vibe/evals/builtin/*.yaml`)
  - Schema migration: auto-adds missing columns (`total_tokens`, `latency_seconds`)
  - Tag validation: enforces `subsystem=`, `difficulty=`, `category=` tags

---

### 2.5 Eval Layer (`vibe/evals/`)

#### `runner.py` — Eval Execution Engine
- **`EvalRunner`**: runs `EvalCase` through `QueryLoop`, validates expectations
  - **Assertion types**: `file_exists`, `file_contains`+`contains_text`, `stdout_contains`, `tool_called`, `tool_sequence`, `no_tool_called`, `context_truncated`, `response_contains`, `response_contains_any`, `min_response_length`, `metrics_threshold`
  - **Observability integration**: spans for `eval_case`, `llm_call`, `tool_execution`, `assertion_check`
  - **Concurrency**: semaphore-controlled parallel case execution
  - **Result recording**: persists to `EvalStore` with token usage and latency

#### `observability.py` — Metrics & Tracing
- **`Observability`**: OpenTelemetry-style observability (NOT a singleton by design)
  - **Metrics**: counter, gauge, histogram with label support
  - **Spans**: parent-child trace spans with contextvar-based active span tracking
  - **Export**: JSON export for metrics (`p50/p95/p99`) and traces
  - **Global default**: `Observability.get_default()` for convenience; separate instances for parallel runs

#### `model_registry.py` — Model Profile Registry
- **`ModelRegistry`**: profiles for different LLM endpoints
  - Each profile: `model_id`, `base_url`, `api_key_env_var`, `timeout`, `cost_per_1k_tokens`
  - Post-Phase-5: neutral default (Ollama at `http://localhost:11434`), no vendor lock-in

#### `multi_model_runner.py` — Multi-Model Benchmarking
- **`MultiModelRunner`**: runs eval suite across multiple models
  - Parallel execution with per-model `QueryLoopFactory.from_profile()`
  - Graceful per-model failure handling
  - Token usage aggregation + cost estimation

#### `soak_test.py` — Long-Running Stability Testing
- **`SoakTestRunner`**: continuous eval execution for N minutes
  - Checkpoint JSONL writes
  - Degradation detection (pass rate drift, latency drift)
  - RSS memory tracking

---

### 2.6 CLI Layer (`vibe/cli/`)

#### `main.py` — Typer CLI
- **`vibe`**: interactive or single-query mode
  - `--model`, `--server`, `--api-key`, `--working-dir`
  - Commands: `/exit`, `/clear`
- **`vibe eval run`**: run built-in eval cases
  - `--tag` filter, `--limit`, `--model`, `--server`
  - Rich table output with pass/fail status and diff
  - Exit code 1 if any eval fails

---

## 3. Running Flow

### 3.1 Single Query Flow

```
User Input
    │
    ▼
┌─────────────────┐
│  QueryLoop.run  │◄────────────────────────────────────────┐
│  (AsyncIterator)│                                         │
└────────┬────────┘                                         │
         │                                                  │
    ┌────┴────┐                                             │
    ▼         ▼                                             │
 PLANNING   (if initial_query)                              │
    │                                                      │
    ▼                                                      │
 ContextPlanner.plan() ──► select tools/skills/MCPs        │
    │         └──► inject system_prompt_append              │
    ▼                                                      │
 PROCESSING                                                │
    │                                                      │
    ├──► _maybe_compact() ──► ContextCompactor            │
    │         └──► if over budget: truncate/summarize       │
    │                                                      │
    ├──► _select_tools_for_llm() ──► planner-filtered set  │
    │         └──► safety fallback: all tools if empty      │
    │                                                      │
    ├──► llm.complete() ──► LLMClient + retry + fallback   │
    │                                                      │
    └──► Response?                                         │
              │                                            │
      ┌───────┴───────┐                                    │
      ▼               ▼                                    │
  tool_calls      content only                             │
      │               │                                    │
      ▼               ▼                                    │
 TOOL_EXEC    _process_content_response()                  │
      │               │                                    │
      ▼               ▼                                    │
 _execute_tool_calls()  FeedbackEngine.self_verify()       │
      │               │                                    │
      ├──► HookPipeline.run_pre_hooks()                    │
      ├──► ToolSystem.execute_tool() / MCPBridge           │
      ├──► HookPipeline.run_post_hooks()                   │
      │               │                                    │
      ▼               ▼                                    │
 SYNTHESIZING    score < threshold?                        │
      │               │                                    │
      │               ├──► YES: inject feedback message ───┘
      │               │         (continue loop)
      │               └──► NO: COMPLETED
      │
      └──► Yield QueryResult
                │
                ▼
        Continue loop? ──► max_iterations? ──► INCOMPLETE
                │
                └──► STOPPED / ERROR / COMPLETED
```

### 3.2 Eval Run Flow

```
vibe eval run
    │
    ▼
EvalStore.load_builtin_evals() ──► List[EvalCase]
    │
    ▼
EvalRunner.run_all(cases)
    │
    ├──► Semaphore(max_concurrency=3)
    │
    └──► For each case:
              │
              ├──► query_loop.clear_history()
              ├──► Observability.start_span("eval_case")
              ├──► query_loop.run(initial_query=case.input.prompt)
              │         └──► Collect all QueryResults
              ├──► Assertion checks:
              │         ├──► file_exists, file_contains, stdout_contains
              │         ├──► tool_called, tool_sequence, no_tool_called
              │         ├──► response_contains, min_response_length
              │         └──► metrics_threshold (latency, tokens)
              ├──► Record metrics + spans
              └──► EvalStore.record_result(EvalResult)
    │
    ▼
Rich table output + score percentage
```

### 3.3 Subagent Delegation Flow

```
SyncDelegate.run(tasks)
    │
    ├──► Semaphore(max_workers=3)
    │
    └──► For each DelegateTask:
              │
              ├──► QueryLoopFactory.create() ──► isolated QueryLoop
              ├──► asyncio.wait_for(loop.run(), timeout=task.timeout)
              ├──► Collect outputs + tool results
              ├──► await loop.close()  // resource cleanup
              └──► DelegateResult(success, output, error)
    │
    ▼
List[DelegateResult]
```

---

## 4. System Design

### 4.1 Layered Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      CLI / API Layer                         │
│         (Typer CLI │ optional FastAPI/uvicorn)               │
├─────────────────────────────────────────────────────────────┤
│                       Eval Layer                             │
│   EvalRunner │ MultiModelRunner │ SoakTestRunner            │
│   Observability │ ModelRegistry                              │
├─────────────────────────────────────────────────────────────┤
│                      Harness Layer                           │
│   QueryLoop ──► ContextPlanner │ FeedbackEngine             │
│   HookPipeline │ InstructionLoader │ SyncDelegate            │
├─────────────────────────────────────────────────────────────┤
│                       Core Layer                             │
│   LLMClient (+ CircuitBreaker + Fallback)                    │
│   ContextCompactor │ ErrorRecovery │ VibeConfig              │
│   QueryLoopFactory                                           │
├─────────────────────────────────────────────────────────────┤
│                       Tool Layer                             │
│   ToolSystem ──► BashTool │ ReadFileTool │ WriteFileTool     │
│   MCPBridge │ SkillManageTool                                │
├─────────────────────────────────────────────────────────────┤
│                      Memory Layer                            │
│   TraceStore (SQLite + optional embeddings)                  │
│   EvalStore (SQLite + YAML cases)                            │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 QueryLoop State Machine

```
                    ┌─────────┐
         ┌─────────►│  IDLE   │◄────────┐
         │          └────┬────┘         │
         │               │ start()       │ clear_history()
         │               ▼               │
         │          ┌─────────┐         │
         │          │ PLANNING│         │
         │          └────┬────┘         │
         │               │               │
         │               ▼               │
         │      ┌─────────────┐          │
         │      │  PROCESSING │◄─────────┤
         │      └──────┬──────┘          │
         │             │                 │
    ┌────┴────┐   ┌────┴────┐   ┌───────┴────────┐
    ▼         │   ▼         │   ▼                │
 COMPLETED◄───┘ TOOL_EXEC  │  ERROR            │
             │   │         │   │                │
             │   ▼         │   ▼                │
             │ SYNTHESIZING│  STOPPED           │
             │   │         │                    │
             └───┘         └────────────────────┘
             │
             ▼
        INCOMPLETE  (max_iterations exhausted)
```

### 4.3 Security Architecture

```
┌────────────────────────────────────────────────────────┐
│                    Security Layers                      │
├────────────────────────────────────────────────────────┤
│ 1. Config: No hardcoded API keys, env-only              │
│ 2. BashTool: exec + shlex (no shell)                   │
│ 3. BashTool: Unquoted metacharacter rejection           │
│ 4. BashTool: Regex denylist (rm -rf, sudo, curl|bash)  │
│ 5. BashTool: Whitelist mode (optional)                 │
│ 6. FileTool: Path jail via relative_to()               │
│ 7. HookPipeline: Permission gate for destructive ops   │
│ 8. HookPipeline: Policy hooks for command patterns     │
│ 9. SkillManageTool: Path traversal protection          │
│ 10. MCPBridge: Connection-scoped execution             │
└────────────────────────────────────────────────────────┘
```

### 4.4 Data Flow Diagram (Eval Run with Observability)

```
┌──────────┐     ┌─────────────┐     ┌─────────────┐
│ EvalCase │────►│ EvalRunner  │────►│ QueryLoop   │
│  (YAML)  │     │             │     │             │
└──────────┘     └──────┬──────┘     └──────┬──────┘
                        │                   │
                        │            ┌──────┴──────┐
                        │            ▼             ▼
                        │      LLMClient    ToolSystem
                        │      (retry +    (bash/file/
                        │       fallback)    MCP)
                        │            │             │
                        │            └──────┬──────┘
                        │                   │
                        │            ┌──────┴──────┐
                        │            ▼             ▼
                        │    Observability    EvalStore
                        │    (spans/metrics)  (SQLite)
                        │            │             │
                        └────────────┴─────────────┘
                                     │
                                     ▼
                              ┌─────────────┐
                              │ EvalResult  │
                              │ (pass/fail) │
                              └─────────────┘
```

---

## 5. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Async vs Sync** | Core fully async (`asyncio`) | LLM I/O bound; enables parallel tool execution, eval runs, subagents |
| **LLM API style** | OpenAI-compatible `/v1/chat/completions` | De-facto standard; works with Ollama, vLLM, proxies |
| **State management** | Explicit `QueryState` enum | Prevents ambiguous states; distinguishes `COMPLETED` from `INCOMPLETE` |
| **Context compaction** | Tiktoken-aware with char fallback | Accuracy when available, portability when not |
| **Tool execution** | `create_subprocess_exec` + `shlex.split` | Eliminates shell injection; metacharacter check as defense-in-depth |
| **Config source** | YAML file + env overrides | Human-readable defaults, machine-friendly overrides (CI-friendly) |
| **Provider default** | Ollama (`http://localhost:11434`) | Neutral, local, no vendor lock-in, zero cost for development |
| **Eval cases** | YAML files with tag schema | Version-controllable, reviewable, easy to extend |
| **Feedback engine** | Self-verification via same LLM | No extra dependencies; rubric-based evaluation for rigor |
| **Observability** | Custom lightweight (not OTel SDK) | Zero external dependencies; JSON export for any backend |
| **Trace storage** | SQLite + optional embeddings | Portable, queryable, semantic search when sentence-transformers available |

---

## 6. Extension Points

### 6.1 Adding a New Tool

```python
class MyTool(Tool):
    def get_schema(self): ...
    async def execute(self, **kwargs) -> ToolResult: ...

tool_system.register_tool(MyTool())
```

### 6.2 Adding a New Eval Case

Create `vibe/evals/builtin/my_case_001.yaml`:

```yaml
id: my_case_001
tags:
  - subsystem=my_subsystem
  - difficulty=medium
  - category=file_ops
input:
  prompt: "Create a file at /tmp/vibe_test/hello.txt with content 'hello'"
expected:
  file_exists: "/tmp/vibe_test/hello.txt"
  file_contains: "/tmp/vibe_test/hello.txt"
  contains_text: "hello"
```

### 6.3 Adding a Constraint Hook

```python
pipeline = HookPipeline()
pipeline.add_hook(HookStage.PRE_ALLOW, my_custom_hook)
query_loop = QueryLoop(..., hook_pipeline=pipeline)
```

### 6.4 Adding a New Compaction Strategy

```python
compactor = ContextCompactor(
    strategy=SummarizationStrategy.LLM_SUMMARIZE,
    summarize_fn=my_summarize_function,
)
```

### 6.5 Adding a New Model Profile

```python
registry = ModelRegistry()
registry.register(ModelProfile(
    name="my-model",
    model_id="my-model-id",
    base_url="https://api.example.com",
    api_key_env_var="MY_API_KEY",
))
```

---

## 7. File Inventory

| Path | Lines | Purpose |
|------|-------|---------|
| `vibe/core/model_gateway.py` | 339 | LLM client, circuit breaker, fallback |
| `vibe/core/query_loop.py` | 416 | Main agent loop, state machine |
| `vibe/core/context_compactor.py` | 161 | Token budget management |
| `vibe/core/error_recovery.py` | 95 | Retry with backoff |
| `vibe/core/config.py` | 337 | Configuration hierarchy |
| `vibe/core/query_loop_factory.py` | 128 | Wiring factory |
| `vibe/tools/tool_system.py` | 64 | Tool registry |
| `vibe/tools/bash.py` | 225 | Sandboxed bash |
| `vibe/tools/file.py` | 138 | File read/write |
| `vibe/tools/mcp_bridge.py` | ~150 | MCP server bridge |
| `vibe/tools/skill_manage.py` | ~80 | Skill CRUD |
| `vibe/harness/constraints.py` | 133 | Hook pipeline |
| `vibe/harness/feedback.py` | 77 | Self-verification |
| `vibe/harness/instructions.py` | 111 | AGENTS.md + skill loader |
| `vibe/harness/planner.py` | 134 | Pre-LLM context selection |
| `vibe/harness/orchestration/sync_delegate.py` | 120 | Subagent delegation |
| `vibe/harness/memory/trace_store.py` | 232 | Session persistence |
| `vibe/harness/memory/eval_store.py` | 146 | Eval storage |
| `vibe/evals/runner.py` | 315 | Eval execution |
| `vibe/evals/observability.py` | 278 | Metrics & tracing |
| `vibe/evals/model_registry.py` | ~80 | Model profiles |
| `vibe/evals/multi_model_runner.py` | ~120 | Multi-model benchmark |
| `vibe/evals/soak_test.py` | ~150 | Long-running tests |
| `vibe/cli/main.py` | 182 | Typer CLI |

**Total Python LOC:** ~3,500 (core) + ~1,500 (tests)  
**Test count:** 231 tests  
**Builtin eval cases:** 32 YAML cases

---

*Document generated from live codebase inspection. For the latest implementation details, refer to the source files directly.*

# Vibe Agent Configuration Guide

[← Back to README](../README.md)

Vibe Agent is configured via `~/.vibe/config.yaml`. The configuration hierarchy is:

**Defaults → `config.yaml` → Environment Variables (`VIBE_*`)**

For a quick start, run `python -m vibe` once — it will auto-create `~/.vibe/config.yaml` with sane defaults.

---

## Quick Reference

| Section | Key Fields | Env Override |
|---------|-----------|-------------|
| `llm` | `default_model`, `base_url`, `timeout`, `stream`, `show_reasoning`, `fallback_chain` | `VIBE_MODEL`, `VIBE_BASE_URL`, `VIBE_STREAM`, `VIBE_SHOW_REASONING`, `VIBE_FALLBACK_CHAIN` |
| `providers` | `base_url`, `adapter`, `api_key_env_var` | — |
| `models` | `provider`, `model_id` | — |
| `fallback` | `enabled`, `chain`, `circuit_breaker_*` | — *(VIBE_FALLBACK_CHAIN maps to chain)* |
| `compactor` | `max_tokens` | `VIBE_COMPACTOR_MAX_TOKENS` |
| `query_loop` | `max_iterations`, `max_context_tokens` | `VIBE_MAX_ITERATIONS` |
| `security` | `approval_mode` | `VIBE_APPROVAL_MODE` |
| `memory` | `enabled`, `wiki.*`, `pageindex.*`, `reflection.*`, `rlm.*` | — |
| `error_recovery` | `pivotal_retry_enabled`, `max_pivotal_retries` | — |

---

## 1. LLM Defaults

The `llm` section sets global defaults used when no per-model configuration is found.

```yaml
llm:
  default_model: "primary-brain"       # Reference to a model in the 'models' section
  base_url: "http://localhost:11434"   # Fallback base URL if no provider is matched
  api_key_env_var: "LLM_API_KEY"       # Env var containing the API key
  timeout: 120.0                        # Seconds before request timeout
  stream: true                         # Enable token-by-token response streaming
  show_reasoning: true                 # Render internal thinking/reasoning traces
```

**Environment overrides:** `VIBE_MODEL`, `VIBE_BASE_URL`, `VIBE_API_KEY_ENV_VAR`, `VIBE_TIMEOUT`, `VIBE_STREAM` (boolean), `VIBE_SHOW_REASONING` (boolean)

---

## 2. Providers

Providers define connection endpoints and API formats. If no providers are configured, the system synthesizes a default provider from `llm` settings for backward compatibility.

```yaml
providers:
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    adapter: "openai"                    # "openai" or "anthropic"
    api_key_env_var: "OPENROUTER_API_KEY"
    extra_headers:
      "HTTP-Referer": "https://github.com/vibe-agent"
      "X-Title": "Vibe Agent"
  
  anthropic:
    base_url: "https://api.anthropic.com"
    adapter: "anthropic"
    api_key_env_var: "ANTHROPIC_API_KEY"
  
  local_ollama:
    base_url: "http://localhost:11434"
    adapter: "openai"                    # Ollama exposes an OpenAI-compatible API
    api_key_env_var: ""                  # No key needed for local Ollama
```

### Provider Fields

| Field | Description |
|-------|-------------|
| `base_url` | Root URL of the API. Required. |
| `adapter` | `openai` or `anthropic`. Determines request/response format. |
| `api_key_env_var` | Name of the env var holding the API key (not the key itself). |
| `extra_headers` | Custom headers sent with every request (e.g., OpenRouter attribution). |
| `timeout` | Per-provider request timeout in seconds. |
| `default_model` | Default model ID for this provider if none is resolved from `models`. |

---

## 3. Models

Models map friendly semantic names to specific providers and model IDs. The agent uses these names in the fallback chain.

```yaml
models:
  primary-brain:
    provider: "openrouter"
    model_id: "google/gemini-2.0-flash-001"
  
  reliable-fallback:
    provider: "anthropic"
    model_id: "claude-3-5-sonnet-latest"
  
  local-fast:
    provider: "local_ollama"
    model_id: "qwen3:8b"
```

Use a model by name in the CLI:
```bash
python -m vibe --model local-fast "What is QQQ?"
```

---

## 4. Fallback Logic

Configure how the agent handles failures by switching models automatically.

```yaml
fallback:
  enabled: true
  chain:
    - "primary-brain"
    - "reliable-fallback"
    - "local-fast"          # Last resort: local Ollama
  max_retries: 3
  health_check_timeout: 10.0
  circuit_breaker_threshold: 5       # Consecutive failures before opening breaker
  circuit_breaker_cooldown: 60.0     # Seconds in open state before half-open probe
```

**How circuit breakers work:** After `circuit_breaker_threshold` consecutive errors on a model, it enters a 60-second cooldown. The system tries the next model in the chain. After cooldown, one probe request is sent; if it succeeds, the breaker closes.

**Environment overrides:** `VIBE_FALLBACK_CHAIN` (comma-separated), `VIBE_CB_THRESHOLD`, `VIBE_CB_COOLDOWN` *(Note: `VIBE_FALLBACK_ENABLED` is not currently implemented in environment variables)*

---

## 5. Context Compaction

Controls how the agent manages token limits in long conversations.

```yaml
compactor:
  max_tokens: 8000
  chars_per_token: 4.0
  preserve_recent: 4         # Always keep the last N message pairs
  max_chars_per_msg: 4000    # Truncate individual messages beyond this
```

**Strategies applied in order:** TRUNCATE → LLM_SUMMARIZE → OFFLOAD → DROP

**Environment overrides:** `VIBE_COMPACTOR_MAX_TOKENS`, `VIBE_COMPACTOR_CHARS_PER_TOKEN`, `VIBE_COMPACTOR_PRESERVE_RECENT`, `VIBE_COMPACTOR_MAX_CHARS`

---

## 6. Query Loop

Limits on the state machine's iteration depth and feedback loops.

```yaml
query_loop:
  feedback_threshold: 0.7      # Score below this triggers a feedback retry
  max_feedback_retries: 1      # Max times to retry with feedback before stopping
  max_iterations: 50           # Hard cap on tool-call cycles per session
  max_context_tokens: 8000     # Token budget for LLM context window
```

**Environment overrides:** `VIBE_FEEDBACK_THRESHOLD`, `VIBE_MAX_FEEDBACK_RETRIES`, `VIBE_MAX_ITERATIONS`, `VIBE_MAX_CONTEXT_TOKENS`

---

## 7. Retry

Global retry settings for transient network errors.

```yaml
retry:
  max_retries: 2
  initial_delay: 1.0           # Seconds; doubles with exponential backoff + jitter
```

**Environment overrides:** `VIBE_RETRY_MAX`, `VIBE_RETRY_DELAY`

---

## 8. Eval

Evaluation suite configuration.

```yaml
eval:
  default_cases_dir: "vibe/evals/builtin"
  scorecard_dir: "~/.vibe/scorecards"
  soak_default_duration_minutes: 60.0
  soak_default_cpm: 6.0
```

**Environment overrides:** `VIBE_EVAL_CASES_DIR`, `VIBE_SCORECARD_DIR`, `VIBE_SOAK_DURATION`, `VIBE_SOAK_CPM`

---

## 9. Logging

```yaml
logging:
  enabled: true
  log_dir: "~/.vibe/logs"
  max_file_size_mb: 10
  retention_days: 5
  level: "INFO"               # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

**Environment overrides:** `VIBE_LOGGING_ENABLED`, `VIBE_LOG_DIR`, `VIBE_LOG_MAX_SIZE`, `VIBE_LOG_RETENTION`

---

## 10. Security

Security execution control configuration.

```yaml
security:
  approval_mode: "smart"               # "manual", "smart", or "auto"
  dangerous_patterns_enabled: true
  secret_redaction: true
  audit_logging: true
  fail_closed: true                    # Security component failure defaults to deny

  file_safety:
    write_denylist_enabled: true
    read_blocklist_enabled: true
    # safe_root: "/path/to/allowed/workspace"  # Optional: restrict all writes here

  env_sanitization:
    enabled: true
    block_path_overrides: true
    strip_shell_env: true
    secret_prefixes:
      - "*_API_KEY"
      - "*_TOKEN"
      - "*_SECRET"
      - "AWS_*"
      - "GITHUB_*"

  sandbox:
    backend: "local"                   # "local", "docker", or "ssh"
    auto_approve_in_sandbox: false

  audit:
    log_path: "~/.vibe/logs/security.log"
    max_events: 10000
    redact_in_logs: true
```

**Environment override:** `VIBE_APPROVAL_MODE`

### Approval Modes

| Mode | Behavior |
|------|----------|
| `manual` | Always prompt the user before executing flagged commands |
| `smart` | Use LLM heuristics to auto-approve benign false-positives (default) |
| `auto` | Skip approval entirely — **not recommended for production** |

---

## 11. Planner

Controls how the agent selects tools and skills for a query before calling the LLM.

```yaml
planner:
  enabled: true
  use_embeddings: true
  embedding_model_path: "/path/to/cc.en.50.bin"   # fastText model for semantic routing
  llm_routing: false                               # Use LLM instead of embeddings (slower)
  cache_ttl: 3600                                  # Seconds to cache plan results
  max_llm_tools: 10                                # Max tool schemas passed to LLM per call
```

**Environment overrides:** `VIBE_PLANNER_ENABLED`, `VIBE_PLANNER_USE_EMBEDDINGS`, `VIBE_PLANNER_EMBEDDING_MODEL_PATH`, `VIBE_PLANNER_LLM_ROUTING`

---

## 12. Tripartite Memory System

The Tripartite Memory System automatically extracts structured knowledge from every completed session and stores it in a Markdown-based wiki. It has three sub-systems: **LLMWiki** (storage), **PageIndex** (routing/search), and **RLM Analyzer** (telemetry).

### Step-by-step Setup

**Step 1: Enable the system**

Memory is enabled by default (`memory.enabled: true`) and works out of the box with stdlib + SQLite only — no optional extras required. To disable it:
```yaml
memory:
  enabled: false
```

**Step 2: Configure the Wiki storage**
```yaml
memory:
  wiki:
    base_path: "~/.vibe/wiki"      # Where .md pages are stored
    auto_extract: true             # Automatically extract knowledge after every session (default: true)
    novelty_threshold: 0.5        # 0.0–1.0: skip items too similar to existing pages
    confidence_threshold: 0.8     # 0.0–1.0: skip low-confidence items
    default_ttl_days: 30          # Draft pages auto-expire after this many days
    extraction_batch_size: 5      # Max knowledge items extracted per session
    extraction_timeout_seconds: 30.0
```

**Step 3: (Optional) Add a FlashLLM for quality gates**

A cheap, local model is used for contradiction detection and confidence scoring — this avoids expensive API calls for quality gate operations.

```yaml
memory:
  wiki:
    flash_model:
      base_url: "http://localhost:11434/v1"  # Local Ollama endpoint
      model: "qwen3:1.7b"                    # A small, fast model
      timeout: 15.0
```

To run the flash model locally:
```bash
ollama pull qwen3:1.7b
```

**Step 4: Configure PageIndex routing**
```yaml
memory:
  pageindex:
    index_path: "~/.vibe/memory/index.json"
    max_nodes_per_index: 100       # Split index nodes beyond this size
    token_threshold: 4000          # BM25 token threshold for splitting
    routing_timeout_seconds: 2.0   # Fail-safe: don't block user if index is slow
    routing_min_confidence: 0.3    # Drop routed nodes below this confidence (0.0–1.0)
```

**Step 5: Configure trajectory reflection**

After every session (including failed ones), the reflector distills up to `max_lessons` reusable lessons from the trajectory and curates them into the wiki as `lesson`-tagged pages with `helpful`/`harmful` counters. Similar lessons merge into existing pages instead of creating duplicates, and lessons become routable into future prompts immediately.

```yaml
memory:
  reflection:
    enabled: true               # Post-session Reflector→Curator pipeline (default: true)
    max_lessons: 3              # Max lessons distilled per session (1–10)
    min_transcript_chars: 400   # Trivial sessions shorter than this are skipped
    max_transcript_chars: 12000 # Trajectory transcript bound sent to the LLM
    merge_title_overlap: 0.7    # Title word-overlap threshold for lesson dedup (0.0–1.0)
    min_generality: 3           # Drop lessons the LLM self-scores below this generality
                              # (1 = instance-specific, 5 = reusable principle; missing
                              # scores are accepted — fail-open)
    compact_min_cluster: 3      # Min similar lesson pages per compaction cluster
    compact_overlap: 0.5        # Title+tag word-overlap threshold for clustering (0.0–1.0)
```

Lesson pages accumulate one per session. `vibe memory wiki compact` clusters similar
lessons (title+tag word overlap) and merges each cluster of at least
`compact_min_cluster` pages into one principle-level page via a single LLM synthesis
call — summing helpful/harmful counters, unioning citations, taking the max generality.
Merged members are archived (never deleted) and excluded from prompt injection. When
lesson pages exceed `compact_min_cluster * 3`, `vibe memory status` prints a hint to
run compaction.

**Error recovery: pivotal retry**

When the same tool call fails twice with identical arguments, the loop marks the pivotal
turn and issues one bounded, guided retry of just that call — the correct prefix is
reused, no re-planning. Security denials are never retried; failures inside the retry
machinery fall back to prior behavior.

```yaml
error_recovery:
  pivotal_retry_enabled: true   # One guided retry per failing call signature (default: true)
  max_pivotal_retries: 1        # Hard bound per call signature per run (default: 1)
```

**Step 6: Configure RLM (Recursive Language Model) analysis**

The RLM analyzer monitors session telemetry and logs a recommendation when your conversations become large/complex enough to warrant local model fine-tuning.

When training data is exported, failed sessions can be relabeled instead of discarded (AgentHER-style hindsight relabeling): each failed session gets one LLM call proposing an achievable alternative goal the trajectory actually demonstrates. The corrected pair is exported with `relabeled: true` and the original goal kept for provenance; low-confidence or incoherent sessions are discarded. Relabeled and discarded counts are logged separately.

```yaml
memory:
  rlm:
    enabled: true
    trigger_threshold_chars: 100000   # Trigger if sessions exceed this character count
    trigger_threshold_compaction_pct: 0.3  # Trigger if >30% of sessions needed compaction
    trigger_window_sessions: 50       # Look at last N sessions
    min_sessions_before_trigger: 10   # Minimum sessions required before analyzing
    relabel_failures: true            # Relabel failed sessions with an achievable goal
    relabel_min_confidence: 0.7       # Discard relabels below this confidence (0.0-1.0)
```

### Complete Tripartite Memory Example

```yaml
memory:
  enabled: true
  
  wiki:
    base_path: "~/.vibe/wiki"
    auto_extract: true
    novelty_threshold: 0.5
    confidence_threshold: 0.8
    default_ttl_days: 30
    extraction_batch_size: 5
    extraction_timeout_seconds: 30.0
    flash_model:
      base_url: "http://localhost:11434/v1"
      model: "qwen3:1.7b"
      timeout: 15.0

  pageindex:
    index_path: "~/.vibe/memory/index.json"
    max_nodes_per_index: 100
    token_threshold: 4000
    routing_timeout_seconds: 2.0
    routing_min_confidence: 0.3

  reflection:
    enabled: true
    max_lessons: 3
    min_transcript_chars: 400
    max_transcript_chars: 12000
    merge_title_overlap: 0.7
    min_generality: 3
    compact_min_cluster: 3
    compact_overlap: 0.5

  rlm:
    enabled: true
    trigger_threshold_chars: 100000
    trigger_threshold_compaction_pct: 0.3
    trigger_window_sessions: 50
    min_sessions_before_trigger: 10
    relabel_failures: true
    relabel_min_confidence: 0.7
```

### Checking Memory Status

```bash
# View wiki page counts and 24h telemetry summary
vibe memory status

# List wiki pages
vibe memory wiki list --status verified

# Expire old drafts
vibe memory wiki expire --days 30

# Merge clusters of similar lesson pages into principle-level lessons
vibe memory wiki compact
```

---

## 13. Trace Store

Episodic session memory — stores full conversation traces for replay and analysis.

```yaml
trace_store:
  enabled: true
  storage_type: "sqlite"               # "sqlite", "json", or "memory"
  db_path: "~/.vibe/memory/traces.db"
  max_entries: 10000
  retention_days: 30
```

**Environment overrides:** `VIBE_TRACE_STORE_ENABLED`, `VIBE_TRACE_STORE_TYPE`, `VIBE_TRACE_STORE_PATH`, `VIBE_TRACE_STORE_MAX_ENTRIES`, `VIBE_TRACE_STORE_RETENTION`

---

## Full Example Config

Below is a complete working config for a local Ollama setup with memory enabled:

```yaml
llm:
  default_model: "local-fast"
  base_url: "http://localhost:11434"
  timeout: 120.0
  stream: true
  show_reasoning: true

providers:
  ollama:
    base_url: "http://localhost:11434"
    adapter: "openai"

models:
  local-fast:
    provider: "ollama"
    model_id: "qwen3:8b"

fallback:
  enabled: false

security:
  approval_mode: "smart"

memory:
  enabled: true
  wiki:
    auto_extract: true
    flash_model:
      model: "qwen3:1.7b"
  rlm:
    enabled: true
```

---

*Last updated: 2026-05-23 | [Back to README](../README.md)*

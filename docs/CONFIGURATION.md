# Vibe Agent Configuration Guide

Vibe Agent is configured via `~/.vibe/config.yaml`. For a complete example, see **[Sample Configuration File](./sample_config.yaml)**.

The configuration is hierarchical: **Defaults → `config.yaml` → Environment Variables (`VIBE_*`)**.

---

## 1. LLM Defaults

```yaml
llm:
  default_model: "primary-brain"       # Reference to a model in the 'models' section
  base_url: "http://localhost:11434"   # Fallback if no provider is used
  api_key_env_var: "LLM_API_KEY"       # Env var containing the API key
  timeout: 120.0
```

**Environment overrides:** `VIBE_MODEL`, `VIBE_BASE_URL`, `VIBE_API_KEY_ENV_VAR`, `VIBE_TIMEOUT`

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
```

### Provider Fields

| Field | Description |
|-------|-------------|
| `base_url` | Root URL of the API. |
| `adapter` | `openai` or `anthropic`. |
| `api_key_env_var` | Env var for the API key. |
| `extra_headers` | Custom headers for every request (e.g., OpenRouter rankings). |
| `timeout` | Request timeout in seconds. |
| `default_model` | Default model ID for this provider. |

---

## 3. Models

Models map friendly names to specific providers and model IDs.

```yaml
models:
  primary-brain:
    provider: "openrouter"
    model_id: "google/gemini-2.0-flash-001"
  
  reliable-fallback:
    provider: "anthropic"
    model_id: "claude-3-5-sonnet-latest"
```

---

## 4. Fallback Logic

Configure how the agent handles failures by switching models.

```yaml
fallback:
  enabled: true
  chain:
    - "primary-brain"
    - "reliable-fallback"
  max_retries: 3
  health_check_timeout: 10.0
  circuit_breaker_threshold: 5       # Failures before opening breaker
  circuit_breaker_cooldown: 60.0     # Seconds before half-open probe
```

**Environment overrides:** `VIBE_FALLBACK_ENABLED`, `VIBE_FALLBACK_CHAIN` (comma-separated), `VIBE_FALLBACK_RETRIES`, `VIBE_CB_THRESHOLD`, `VIBE_CB_COOLDOWN`

---

## 5. Context Compaction

Controls how the agent manages token limits.

```yaml
compactor:
  max_tokens: 8000
  chars_per_token: 4.0
  preserve_recent: 4
  max_chars_per_msg: 4000
```

**Environment overrides:** `VIBE_COMPACTOR_MAX_TOKENS`, `VIBE_COMPACTOR_CHARS_PER_TOKEN`, `VIBE_COMPACTOR_PRESERVE_RECENT`, `VIBE_COMPACTOR_MAX_CHARS`

---

## 6. Query Loop

Limits on iterations and feedback loops.

```yaml
query_loop:
  feedback_threshold: 0.7
  max_feedback_retries: 1
  max_iterations: 50
  max_context_tokens: 8000
```

**Environment overrides:** `VIBE_FEEDBACK_THRESHOLD`, `VIBE_MAX_FEEDBACK_RETRIES`, `VIBE_MAX_ITERATIONS`, `VIBE_MAX_CONTEXT_TOKENS`

---

## 7. Retry

Global retry settings for transient network errors.

```yaml
retry:
  max_retries: 2
  initial_delay: 1.0
```

**Environment overrides:** `VIBE_RETRY_MAX`, `VIBE_RETRY_DELAY`

---

## 8. Eval

Eval suite configuration.

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
    # safe_root: "/path/to/allowed/workspace"  # Optional

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
| `manual` | Always ask user for approval on flagged commands |
| `smart` | Use LLM to auto-approve benign false positives (default) |
| `auto` | Skip approval entirely (not recommended for production) |

---

## 11. Planner

Controls how the agent selects tools and skills for a query.

```yaml
planner:
  enabled: true
  use_embeddings: true
  embedding_model_path: "/path/to/cc.en.50.bin"
  llm_routing: false
  cache_ttl: 3600                      # Seconds to cache plan results
  max_llm_tools: 10                    # Max tools to pass to LLM
```

**Environment overrides:** `VIBE_PLANNER_ENABLED`, `VIBE_PLANNER_USE_EMBEDDINGS`, `VIBE_PLANNER_EMBEDDING_MODEL_PATH`, `VIBE_PLANNER_LLM_ROUTING`

---

## 12. Tripartite Memory

Configuration for the Tripartite Memory System (LLMWiki, KnowledgeExtractor, RLM Analyzer).

```yaml
memory:
  enabled: true
  
  wiki:
    base_path: "~/.vibe/wiki"
    auto_extract: true
    novelty_threshold: 0.5
    confidence_threshold: 0.8
    flash_model:
      base_url: "http://localhost:11434/v1"
      model: "qwen3:1.7b"
      timeout: 15.0

  pageindex:
    index_path: "~/.vibe/memory/index.json"
    max_nodes_per_index: 100
    token_threshold: 4000
    routing_timeout_seconds: 2.0

  rlm:
    enabled: true
    trigger_threshold_chars: 100000
    trigger_threshold_compaction_pct: 0.3
    trigger_window_sessions: 50
    min_sessions_before_trigger: 10
```

---

## 13. Trace Store

Configuration for episodic session memory.

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

*Last updated: 2026-04-26*

# Vibe Agent Configuration Guide

Vibe Agent is configured via `~/.vibe/config.yaml`. For a complete, ready-to-use example with multiple providers and fallback tiers, see the **[Sample Configuration File](./sample_config.yaml)**.

The configuration is divided into sections for LLM settings, providers, models, and operational parameters.

## 1. LLM Defaults

```yaml
llm:
  default_model: "primary-brain"  # Reference to a model in the 'models' section
  base_url: "http://localhost:11434" # Fallback if no provider is used
  timeout: 120.0
```

## 2. Providers

Providers define connection endpoints and API formats.

```yaml
providers:
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    adapter: "openai"
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
| `api_key_env_var`| Env var for the API key. |
| `extra_headers` | Custom headers for every request (e.g., for OpenRouter rankings). |

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

## 4. Fallback Logic

Configure how the agent handles failures by switching models.

```yaml
fallback:
  enabled: true
  chain:
    - "primary-brain"
    - "reliable-fallback"
  max_retries: 2
  health_check_timeout: 10.0
```

## 5. Other Settings

### Context Compaction
Controls how the agent manages token limits.
```yaml
compactor:
  max_tokens: 8000
  chars_per_token: 4.0
  preserve_recent: 4
  max_chars_per_msg: 4000
```

### Query Loop
Limits on iterations and feedback loops.
```yaml
query_loop:
  feedback_threshold: 0.7
  max_feedback_retries: 1
  max_iterations: 50
```

### Retry
Global retry settings for transient network errors.
```yaml
retry:
  max_retries: 2
  initial_delay: 1.0
```

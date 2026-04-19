# LLM Connector Plan — Multi-Provider Support for vibe-agent

**Author:** Hermes Agent  
**Date:** 2026-04-18  
**Status:** Pending Gemini CLI Review  
**Depends on:** None (self-contained)  
**Scope:** vibe-agent core LLM layer + eval infrastructure  

---

## 1. Goal

Enable vibe-agent to route LLM requests to **multiple providers simultaneously** — Applesay, Kimi, Ollama, Anthropic, OpenRouter — with clean provider selection, cross-provider fallback, and eval benchmarking across all configured providers.

Specifically support **Anthropic API format** (Kimi coding endpoint, direct Anthropic) alongside the existing OpenAI-compatible path.

---

## 2. Scope

### In Scope
- **Provider adapter pattern**: Pluggable adapters for OpenAI-compatible and Anthropic-native APIs
- **Multi-provider config**: `providers` section in `~/.vibe/config.yaml` with per-provider `base_url`, `adapter`, `api_key_env_var`, `default_model`
- **Provider selection**: `VIBE_PROVIDER` env var + `--provider` CLI flag
- **Cross-provider fallback**: Fallback chains can hop across providers (e.g., Kimi → Applesay → Ollama)
- **Per-provider health checks**: Adapter-specific health probe endpoints
- **Eval infrastructure integration**: Multi-model benchmarking (`vibe benchmark`) + scorecard generation
- **Related bug fixes only**: Issues uncovered during this refactor (e.g., hardcoded OpenAI paths, health check coupling)

### Out of Scope (for this plan)
- CLI-agent-as-provider (calling `kimi-cli` / `claude` subprocess as LLM backend)
- Provider-specific prompt templating or system prompt injection
- Cost-based intelligent routing
- Streaming response support (future phase)

---

## 3. Current State Analysis

| Component | Current | Problem |
|-----------|---------|---------|
| `LLMClient` | Hardcoded OpenAI `/v1/chat/completions` | Cannot talk to Anthropic or custom endpoints |
| `VibeConfig` | Single `llm:` block | No way to define multiple provider profiles |
| `ModelRegistry` | One built-in profile (Ollama) | Profiles not populated from config |
| `ModelHealthChecker` | Hardcoded `/v1/models` + `/v1/chat/completions` | Breaks on non-OpenAI providers |
| `QueryLoopFactory` | Accepts `base_url`, `model`, `api_key` | Works with profiles but no profile selection mechanism |
| Eval runner | Single model per run | No multi-model comparison |

---

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VibeConfig                                       │
│  providers: {applesay, kimi, ollama, anthropic}                              │
│  active_provider: str  ← VIBE_PROVIDER or config.default_provider            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          ProviderRegistry                                     │
│  name → ProviderProfile (base_url, adapter_type, api_key_env_var, ...)       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
   ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
   │   OpenAIAdapter │      │AnthropicAdapter │      │  (future ext)   │
   │                 │      │                 │      │                 │
   │ build_request() │      │ build_request() │      │ build_request() │
   │ parse_response()│      │ parse_response()│      │ parse_response()│
   │ health_check()  │      │ health_check()  │      │ health_check()  │
   └─────────────────┘      └─────────────────┘      └─────────────────┘
            │                         │                         │
            └─────────────────────────┼─────────────────────────┘
                                      ▼
                           ┌─────────────────┐
                           │    LLMClient    │
                           │  (adapter-agnostic│
                           │   HTTP client)   │
                           └─────────────────┘
```

### 4.1 Adapter Interface

```python
class BaseLLMAdapter(ABC):
    @abstractmethod
    def build_request(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int | None,
        tools: list[dict] | None,
        tool_choice: str,
    ) -> tuple[str, dict, dict]:  # (url, headers, json_payload)
        ...

    @abstractmethod
    def parse_response(self, response_json: dict) -> LLMResponse:
        ...

    @abstractmethod
    def health_check_endpoints(self, model_id: str) -> list[str]:
        """Return URLs to probe for availability, in priority order."""
        ...

    @abstractmethod
    def parse_health_response(self, endpoint: str, response_json: dict) -> bool:
        """Return True if model is available."""
        ...

    @abstractmethod
    def extract_system_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Extract system message content from messages array.
        
        Returns (system_content, remaining_messages).
        For Anthropic: extracts role=system into top-level param.
        For OpenAI: returns (None, messages) unchanged.
        Called by structured_output and build_request.
        """
        ...
```

### 4.2 Provider Profile

```yaml
# ~/.vibe/config.yaml
providers:
  ollama:
    base_url: "http://localhost:11434"
    adapter: "openai"
    api_key_env_var: "OLLAMA_API_KEY"
    default_model: "llama3.2"
    timeout: 120.0
    tags: ["local", "free", "ci"]

  applesay:
    base_url: "http://ai-api.applesay.cn"
    adapter: "openai"
    api_key_env_var: "APPLESAY_API_KEY"
    default_model: "qwen3.5-plus"
    timeout: 120.0
    tags: ["proxy", "multi-model"]

  kimi:
    base_url: "https://api.kimi.com"
    adapter: "openai"
    api_key_env_var: "KIMI_API_KEY"
    default_model: "kimi-k2.5"
    timeout: 120.0
    tags: ["coding", "kimi"]

  anthropic:
    base_url: "https://api.anthropic.com"
    adapter: "anthropic"
    api_key_env_var: "ANTHROPIC_API_KEY"
    default_model: "claude-sonnet-4-6"
    timeout: 120.0
    tags: ["anthropic", "direct"]

# Optional: explicit model overrides for benchmarking
# If omitted, each provider's default_model becomes one ModelProfile
models:
  claude-opus:
    provider: "anthropic"
    model_id: "claude-opus-4"
    tags: ["expensive", "reasoning"]

# Backward compatibility: if providers missing, fall back to single llm: block
llm:
  default_model: "default"
  base_url: "http://localhost:11434"
  api_key_env_var: "LLM_API_KEY"
  timeout: 120.0

# Which provider to use by default
active_provider: "ollama"  # or VIBE_PROVIDER env var
```

---

## 5. Implementation Phases

### Phase A: Provider Adapter Layer (~3 hrs)

**Files:**
- `vibe/adapters/__init__.py`
- `vibe/adapters/base.py` — `BaseLLMAdapter` abstract class
- `vibe/adapters/openai.py` — `OpenAIAdapter` (extracted from `LLMClient`)
- `vibe/adapters/anthropic.py` — `AnthropicAdapter` (new)
- `vibe/adapters/registry.py` — `ADAPTER_REGISTRY` mapping adapter name → class

**Changes to existing:**
- `vibe/core/model_gateway.py`: Replace hardcoded OpenAI logic with adapter delegation
- `LLMClient.__init__`: Accept `adapter: BaseLLMAdapter` instead of just `base_url`
- `LLMClient._try_complete()`: Use `adapter.build_request()` + `adapter.parse_response()`

**Anthropic-specific details:**
- Endpoint: `{base_url}/v1/messages`
- Request format: `{"model": "...", "messages": [...], "max_tokens": ...}` (note: `max_tokens` is required)
- Response format: `{"content": [{"type": "text", "text": "..."}], "usage": {"input_tokens": ..., "output_tokens": ...}}`
- Tool calls: Different nesting structure than OpenAI
- System messages: **Must be extracted from `messages` array and passed as top-level `system` parameter.** `AnthropicAdapter.extract_system_messages()` handles this; `LLMClient.structured_output()` must use it instead of blindly prepending `{"role": "system", ...}`.

**Related bug fixes in this phase:**
- Fix health check hardcoding (move into adapters)
- Fix `ErrorRecovery.handle_error` brittle string matching if touched during refactor
- Fix `LLMClient.structured_output()` system message handling for non-OpenAI adapters

### Phase B: Multi-Provider Config (~2 hrs)

**Files:**
- `vibe/core/config.py`: Add `ProviderConfig` dataclass, `providers` dict, `active_provider` field
- `vibe/core/provider_registry.py`: New — `ProviderRegistry` class (load from config, resolve by name)

**Behavior:**
1. Load `providers` from YAML
2. If missing, auto-migrate single `llm:` block into a single `default` provider
3. Resolve `active_provider`: `VIBE_PROVIDER` env var → config `active_provider` → `"default"`
4. `VibeConfig.get_active_provider()` → `ProviderConfig`
5. `VibeConfig.resolve_api_key(provider)` → reads from `provider.api_key_env_var`

**Backward compatibility:**
- Existing configs without `providers:` continue to work exactly as before
- Old env vars (`VIBE_BASE_URL`, `VIBE_MODEL`) still override the `default` provider

### Phase C: Health Check Per Provider (~1 hr)

**Files:**
- `vibe/core/health_check.py`: Refactor to resolve adapter dynamically per model

**Design change (from Gemini review):**
`ModelHealthChecker` must **not** hold a single adapter — fallback chains can cross providers (Kimi → Anthropic). Instead:

```python
class ModelHealthChecker:
    def __init__(self, provider_registry: ProviderRegistry):
        self.provider_registry = provider_registry

    async def check_available(self, profile: ModelProfile, timeout: float = 10.0) -> bool:
        provider = self.provider_registry.get(profile.provider)
        adapter = ADAPTER_REGISTRY[provider.adapter]()
        for endpoint in adapter.health_check_endpoints(profile.model_id):
            # probe endpoint...
```

**Changes:**
- `check_available()` takes a `ModelProfile` and resolves the correct adapter via `ProviderRegistry`
- `resolve_model()` iterates fallback chain, dynamically resolving adapter per profile
- No adapter stored in `__init__`

### Phase D: Cross-Provider Fallback & Factory Wiring (~2 hrs)

**Files:**
- `vibe/evals/model_registry.py`: Populate from `ProviderRegistry` + allow multiple models per provider
- `vibe/core/query_loop_factory.py`: Add `from_provider(name)` classmethod; pass `adapter_type` through
- `vibe/cli/main.py`: Add `--provider` CLI flag

**Provider vs. Model separation (from Gemini review):**
A single provider config can host multiple models. Do **not** force 1:1 mapping.

```yaml
providers:
  anthropic:
    base_url: "https://api.anthropic.com"
    adapter: "anthropic"
    api_key_env_var: "ANTHROPIC_API_KEY"

models:
  claude-sonnet:
    provider: "anthropic"
    model_id: "claude-sonnet-4-6"
  claude-opus:
    provider: "anthropic"
    model_id: "claude-opus-4"
```

- `ProviderRegistry` manages connection configs (`providers:`)
- `ModelRegistry` manages evaluation targets (`models:` or auto-generated from provider `default_model`)
- `ModelProfile` gains `provider: str` field referencing provider name
- `QueryLoopFactory.from_provider("anthropic", model_id="claude-sonnet-4-6")` creates `LLMClient` with correct adapter + base_url + api_key

**Changes:**
- `QueryLoopFactory.__init__` accepts `adapter_type: str` (resolved from provider config)
- `create_llm()` instantiates adapter from `ADAPTER_REGISTRY[self.adapter_type]`
- `ModelRegistry` loads all providers from config as `ModelProfile`s; can also load explicit `models:` overrides
- Fallback chain can include profiles from different providers (different `base_url`s, different adapters)

### Phase E: Eval Infrastructure — Multi-Model Benchmark (~3 hrs)

**Files:**
- `vibe/evals/multi_model_runner.py`: Extend existing to use provider profiles
- `vibe/evals/scorecard.py`: New — generate comparative reports
- `vibe/cli/main.py`: Add `vibe benchmark` command

**Behavior:**
```bash
# Run eval suite across all configured providers
vibe benchmark --providers "kimi,applesay,ollama" --output report.md

# Run in parallel (one QueryLoop per provider)
vibe benchmark --providers "kimi,applesay" --parallel

# CI mode: use provider tagged with "ci"
vibe benchmark --ci
```

**Scorecard output:**
```json
{
  "run_id": "uuid",
  "timestamp": "2026-04-18T22:00:00Z",
  "providers": {
    "kimi": {"passed": 18, "failed": 2, "avg_latency_ms": 4200, "total_cost_usd": 0.12},
    "applesay": {"passed": 19, "failed": 1, "avg_latency_ms": 3800, "total_cost_usd": 0.08},
    "ollama": {"passed": 15, "failed": 5, "avg_latency_ms": 8500, "total_cost_usd": 0.0}
  },
  "by_tag": {
    "security": {"kimi": 1.0, "applesay": 1.0, "ollama": 0.8},
    "multi_step": {"kimi": 0.9, "applesay": 0.95, "ollama": 0.7}
  }
}
```

**Integration with existing:**
- Reuse `run_e2e_evals.py` logic
- Store per-provider results in `EvalStore` with `provider` column
- Generate markdown report for PR comments / human reading

### Phase F: Tests & Validation (~2-3 hrs)

**Unit tests:**
- `tests/adapters/test_openai_adapter.py`: Mocked request building + response parsing
- `tests/adapters/test_anthropic_adapter.py`: Same for Anthropic format
- `tests/core/test_provider_registry.py`: Config loading, provider resolution, env override
- `tests/core/test_health_check.py`: Per-adapter health probes

**Integration tests:**
- `tests/test_multi_provider.py`: Switch provider via `VIBE_PROVIDER`, verify correct endpoint called
- `tests/test_cross_provider_fallback.py`: Primary provider fails, fallback to different provider

**Regression tests:**
- Existing Ollama default works without `providers:` in config
- Existing `VIBE_BASE_URL` / `VIBE_MODEL` env vars still override correctly
- All 230 existing tests still pass

---

## 6. Related Bug Fixes (Only These)

| Bug | File | Fix |
|-----|------|-----|
| Health check hardcoded to OpenAI | `health_check.py` | Move into adapters |
| `ErrorRecovery.handle_error` substring matching | `error_recovery.py` | Use error type enum, not string search |
| `LLMClient` creates new `AsyncClient` per instance | `model_gateway.py` | Optional shared client pool (Phase A follow-up) |
| Factory defaults diverge from config defaults | `query_loop_factory.py` | Read from config, not hardcoded `max_iterations=10` |

---

## 7. Testing & Quality Gates

1. **Before implementation:** Gemini CLI plan review (this document) → user approval
2. **After each phase:** Unit tests for new code + regression check on existing tests
3. **After Phase F:** Gemini CLI code review of all changes
4. **Final approval:** User signs off before merge

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Anthropic adapter complexity (tool calls, system msgs) | Extensive unit tests with real Anthropic response JSON fixtures |
| Config migration breaking existing users | Backward-compat: `llm:` block auto-migrates to `default` provider |
| Cross-provider fallback auth confusion | Each profile carries its own `api_key_env_var`; never share keys across providers |
| Eval cost explosion | `--max-budget-usd` per provider; CI uses cheapest provider only |

---

## 9. Success Criteria

- [ ] `vibe-agent` can complete a chat via Kimi API (OpenAI format)
- [ ] `vibe-agent` can complete a chat via Anthropic API (native format)
- [ ] `vibe benchmark --providers "kimi,anthropic"` produces comparative scorecard
- [ ] Fallback works across providers (Kimi down → Applesay used)
- [ ] Existing single-provider configs work without modification
- [ ] All 230+ tests pass, including new adapter tests
- [ ] Gemini CLI approves the implementation

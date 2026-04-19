# Vibe Agent

Vibe Agent is an open, visual-first interactive CLI agent harness. It is designed to provide a robust, resilient, and secure environment for LLM-based autonomous tasks, independent of any specific model or provider.

## 🚀 Key Functions

-   **Multi-Provider Fallback**: Seamlessly switch between OpenAI, Anthropic, and other providers (via OpenRouter or Ollama) when primary models fail.
-   **Secure Tool Execution**: Sandboxed Bash and File system tools with three-layer security defense and path jailing.
-   **Context Management**: Automated compaction and summarization to handle long-running conversations within token limits.
-   **Eval-Driven Development**: A built-in suite of 30+ evaluation cases to ensure every update maintains performance and stability.
-   **Customizable Skills**: Extend the agent's capabilities with markdown-based skill definitions.

## 🏗️ Architectural Design

Vibe Agent is built on a modular "Harness" architecture:

-   **Query Loop**: A state-machine-driven orchestrator that manages planning, tool use, and feedback.
-   **Model Gateway**: An adapter-based gateway that normalizes different LLM APIs and handles resilience (circuit breakers, retries).
-   **Tool System**: A registry-based system for secure, isolated tool execution.

Read more in the [Architecture Document](docs/ARCHITECTURE.md).

## ⚙️ Configuration

Vibe Agent is configured via `~/.vibe/config.yaml`. It supports defining multiple **Providers** (endpoints) and **Models** (logic names mapped to providers).

```yaml
providers:
  openrouter:
    base_url: "https://openrouter.ai/api/v1"
    adapter: "openai"
    api_key_env_var: "OPENROUTER_API_KEY"

models:
  primary:
    provider: "openrouter"
    model_id: "google/gemini-2.0-flash-001"

fallback:
  enabled: true
  chain: ["primary", "backup-model"]
```

See the [Configuration Guide](docs/CONFIGURATION.md) for full details.

## 🛠️ Usage

### Installation
```bash
pip install -e .
```

### Running the Agent
```bash
# Start an interactive session
python -m vibe
```

### Running Evaluations
```bash
# Run the built-in eval suite
python run_e2e_evals.py
```

## 📚 Documentation Index

-   [Architecture](docs/ARCHITECTURE.md)
-   [Configuration Guide](docs/CONFIGURATION.md)
-   [Roadmap & Plans](docs/ROADMAP.md)
-   [Evaluation Suite](docs/EVALUATION.md)
-   [Code Reviews Archive](docs/REVIEWS.md)
-   [Changelog](CHANGELOG.md)

---

*Vibe Agent is currently in Phase 1 (Core Harness Hardening). See the [Roadmap](docs/ROADMAP.md) for what's next.*

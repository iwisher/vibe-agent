# Vibe Agent

Vibe Agent is an open, visual-first interactive CLI agent harness. It is designed to provide a robust, resilient, and secure environment for LLM-based autonomous tasks, independent of any specific model or provider.

## 🚀 Key Functions

-   **Multi-Provider Fallback**: Seamlessly switch between OpenAI, Anthropic, and other providers (via OpenRouter or Ollama) when primary models fail.
-   **Secure Tool Execution**: Sandboxed Bash and File system tools with three-layer security defense and path jailing.
-   **Context Management**: Automated compaction and summarization to handle long-running conversations within token limits.
-   **Eval-Driven Development**: A built-in suite of 30+ evaluation cases to ensure every update maintains performance and stability.
-   **Phase 2 Skill System**: Native vibe skill format with TOML frontmatter, markdown body, validation, security scanning, and atomic installation.
-   **Tripartite Memory System**: Automated async knowledge extraction, FlashLLM contradiction detection, and telemetry-triggered RLM analysis.
-   **Secret Redaction**: Automatic stripping of API keys (OpenAI, AWS, GitHub, etc.) and passwords from trace stores and logs.
-   **Interactive CLI**: Readline support with persistent history, token metrics display, and rich skill management commands.

## 🏗️ Architectural Design

Vibe Agent is built on a modular "Harness" architecture:

-   **Query Loop**: A state-machine-driven orchestrator that manages planning, tool use, and feedback.
-   **Model Gateway**: An adapter-based gateway that normalizes different LLM APIs and handles resilience (circuit breakers, retries).
-   **Tool System**: A registry-based system for secure, isolated tool execution.
-   **Skill System**: A declarative, markdown-native skill format with validation, approval gating, and sandboxed execution.

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

### Managing Skills
```bash
# Scaffold a new skill
vibe skill create my-skill

# Validate a skill directory
vibe skill validate ./my-skill

# Install from git, tarball, or local path
vibe skill install https://github.com/user/skill-repo.git
vibe skill install ./my-skill

# List installed skills
vibe skill list

# Run a skill with variables
vibe skill run my-skill greeting="Hello World"

# Uninstall a skill
vibe skill uninstall my-skill
```

### Managing Memory
```bash
# Show tripartite memory system status
vibe memory status

# Expire old draft pages
vibe wiki expire --days 30
```

### Running Evaluations
```bash
# Run the built-in eval suite
python run_e2e_evals.py
```

## 🧩 Skill System

Vibe Agent includes a native skill format designed for safe, portable, and versioned automation:

### SKILL.md Format
Skills are defined as markdown files with TOML frontmatter:

```markdown
+++
vibe_skill_version = "2.0.0"
id = "example-skill"
name = "Example Skill"
description = "Does something useful"
category = "devops"
tags = ["deploy", "ci"]

[trigger]
patterns = ["deploy to staging"]
required_tools = ["bash"]

[[steps]]
id = "build"
description = "Build the project"
tool = "bash"
command = "npm run build"

[steps.verification]
exit_code = 0
+++

# Example Skill

## Overview
Describe what this skill does and when to use it.

## Steps
### Step 1: Build
...
```

### Key Components

| Component | Description |
|-----------|-------------|
| `Skill` Models | Pydantic models with validation for ID format, unique step IDs, and required fields |
| `SkillParser` | Parses TOML frontmatter + markdown body into structured `Skill` objects |
| `SkillValidator` | Security scanning for filesystem traversal, phishing URLs, and dangerous script patterns |
| `ApprovalGate` | Protocol supporting CLI interactive approval, `AutoApprove`, and `AutoReject` modes |
| `SkillInstaller` | Atomic installation from git clone, tarball download, or local path with rollback support |
| `SkillExecutor` | Variable substitution, BashTool delegation, and step-by-step verification |
| `SkillManageTool` | Agent-facing tool that validates `SKILL.md` content before writing to disk |

## 📚 Documentation Index

-   [Architecture](docs/ARCHITECTURE.md)
-   [Configuration Guide](docs/CONFIGURATION.md)
-   [Roadmap & Plans](docs/ROADMAP.md)
-   [Evaluation Suite](docs/EVALUATION.md)
-   [Code Reviews Archive](docs/REVIEWS.md)
-   [Changelog](docs/CHANGELOG.md)

---

*Vibe Agent is currently in Phase 2 (Skill System & Platform Hardening). See the [Roadmap](docs/ROADMAP.md) for what's next. Test suite: 337 tests passing.*

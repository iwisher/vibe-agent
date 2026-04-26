# Vibe Agent Project Context

Vibe Agent is an open, visual-first, and LLM-agnostic interactive CLI agent harness. It is designed to provide a robust, resilient, and secure environment for LLM-based autonomous tasks, prioritizing tool safety, context efficiency, and performance stability via a built-in evaluation suite.

## 🏗️ Architecture & Core Components

- **Query Loop (`vibe/core/query_loop.py`)**: A state-machine-driven orchestrator managing transitions: `IDLE` → `PLANNING` → `PROCESSING` → `TOOL_EXECUTION` → `SYNTHESIZING` → `COMPLETED|INCOMPLETE|STOPPED|ERROR`.
- **Model Gateway (`vibe/core/model_gateway.py`)**: An adapter-based gateway (OpenAI, Anthropic) with resilience features like circuit breakers (5 failures → 60s cooldown) and retries.
- **Tool System (`vibe/tools/`)**: Zero-trust execution environment. `bash.py` uses denylists; `file.py` uses path jailing. Security settings (approval mode: `manual`, `smart`, `auto`) are in `vibe/core/config.py`.
- **Skill System (`vibe/harness/skills/`)**: Native skill format using `SKILL.md` (TOML frontmatter + Markdown body). Managed via `vibe skill` commands.
- **Evaluation Suite (`vibe/evals/`)**: Built-in harness with ~50 cases. Regression testing is mandatory (results must stay within 5% of `docs/baseline_scorecard.json`).

## 🛠️ Key Commands

### Development Setup
```bash
pip install -e ".[dev]"  # Install with dev extras (ruff, mypy, black)
```

### Execution
```bash
python -m vibe              # Start interactive session
vibe --debug                # Run with debug logging
python -m vibe "query"      # One-shot query
```

### Testing & Linting
```bash
pytest                      # Run full test suite (~340 tests)
ruff check vibe/ tests/     # Linting
ruff format vibe/ tests/    # Formatting
```

### Evaluation & Evals
```bash
vibe eval run               # Run all built-in evals
vibe eval update-baseline   # Update the performance baseline
python run_e2e_evals.py     # Standalone runner with benchmark/soak modes
```

### Skill Management
```bash
vibe skill list             # List installed skills
vibe skill validate <path>  # Validate a skill directory
vibe skill install <src>    # Install from git/path/tarball
```

## 📝 Development Conventions

- **Environment**: Python 3.11+. Use type hints for all public APIs.
- **Models**: Pydantic v2 for data structures and validation.
- **Formatting**: Ruff with 100-character line limit. CI gates on linting and formatting.
- **Async**: `pytest-asyncio` in `auto` mode (no markers required).
- **Security**: Zero-trust for tools. Security hooks live in `vibe/harness/constraints.py`.
- **Documentation**: Keep `docs/ARCHITECTURE.md`, `docs/CONFIGURATION.md`, and `docs/EVALUATION.md` updated with relevant changes.
- **Reference**: The `archive/` directory is for reference only; **do not** import from it.
- **User State**: Configuration and logs reside in `~/.vibe/`.

## 📂 Directory Structure Highlights

- `vibe/core/`: The heart of the harness (Loop, Config, Gateway, Registry).
- `vibe/adapters/`: Provider-specific implementations (OpenAI, Anthropic).
- `vibe/tools/`: Tool implementations and security logic.
- `vibe/evals/`: Evaluation logic and YAML test cases.
- `vibe/harness/`: High-level orchestration (Skills, Instructions, Feedback).
- `tests/`: Comprehensive test suite reflecting the modular architecture.
- `docs/`: In-depth documentation for all subsystems.

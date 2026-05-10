# Vibe Agent User Manual

Vibe Agent is an open, visual-first, and LLM-agnostic interactive CLI agent harness. It provides a robust, resilient, and secure environment for LLM-based autonomous tasks.

---

## 🚀 Getting Started

### Installation
Install the package in development mode with all optional dependencies:
```bash
pip install -e ".[dev,ingest]"
```

### Basic Usage
Start an interactive session:
```bash
python -m vibe
# or if installed in path
vibe
```

Run a single query:
```bash
vibe "How do I list files in the current directory?"
```

Enable debug mode (prints request details):
```bash
vibe -d "Who am I?"
```

**Interactive Commands:**
- `/exit`: Quit the session.
- `/clear`: Reset conversation history.
- `/verbose`: Toggle detailed progress logging (shows internal state transitions).

---

## 📂 Session Management

Vibe Agent automatically saves session checkpoints, allowing you to resume interrupted tasks.

### List Incomplete Sessions
```bash
vibe session list
```

### Resume a Session
Resume the latest incomplete session:
```bash
vibe session resume
```

Resume a specific session by ID:
```bash
vibe session resume <session-id>
```

---

## 🛠️ Skill System

Skills are pre-defined task templates that the agent can execute. They are defined in `SKILL.md` files.

### List Installed Skills
```bash
vibe skill list
```

### Install a Skill
From a local path:
```bash
vibe skill install ./path/to/skill
```

From a Git repository:
```bash
vibe skill install https://github.com/user/vibe-skill.git
```

### Create a New Skill Scaffold
```bash
vibe skill create my-new-skill
```

### Run a Skill Manually
```bash
vibe skill run my-skill-id var1=value1 var2=value2
```

### Validate a Skill
```bash
vibe skill validate ./path/to/skill
```

---

## 🧠 Tripartite Memory System

The memory system consists of a Wiki (structured knowledge), a PageIndex (routing), and Telemetry (usage logs).

### Wiki Management
List wiki pages:
```bash
vibe memory wiki list
```

Search knowledge base:
```bash
vibe memory wiki search "deployment instructions"
```

Show a specific page:
```bash
vibe memory wiki show <page-id-or-slug>
```

Create/Edit a page:
```bash
vibe memory wiki create --title "Project Notes"
vibe memory wiki edit <page-id>
```

Rebuild the index (for routing):
```bash
vibe memory wiki index rebuild
```

Expire old draft pages:
```bash
vibe memory wiki expire --days 30
```

### Knowledge Ingestion
Import documents (PDF, MD, DOCX) using semantic extraction:
```bash
vibe memory import ./docs/proposal.pdf
```

### Wiki Compilation & Review
Compile recent session traces into candidate wiki pages:
```bash
vibe memory wiki compile --hours 24
```

Review pending pages:
```bash
vibe memory wiki review
```

---

## 🧪 Evaluation Suite

Built-in tools to measure agent performance and prevent regressions.

### Run Standard Evals
```bash
vibe eval run
```

Filter by tag:
```bash
vibe eval run --tag "subsystem=bash"
```

### Update Baseline
Update the performance baseline scorecard used for regression checks:
```bash
vibe eval update-baseline
```

### Soak Testing
Stress test the agent with a long-running loop:
```bash
vibe eval soak --duration 60 --cpm 5
```

### Advanced Evals (Multi-model Benchmark)
Using the standalone `run_e2e_evals.py` script:
```bash
python run_e2e_evals.py benchmark --models "gpt-4o,claude-3-5-sonnet"
```

---

## 🛡️ Security & Workspace Protection

### Approval Modes
Configure how tools are authorized in `.env` or `VIBE_APPROVAL_MODE`:
- `manual`: Always ask for human approval.
- `smart`: Ask for approval only on "risky" commands.
- `auto`: Execute all commands (use with caution).
- `strict`: Deny all risky commands immediately.

### Shadow Workspace (Git Rollback)
Create a checkpoint before a risky operation:
```bash
vibe shadow create <session-id>
```

Restore workspace to the last checkpoint:
```bash
vibe shadow restore <session-id>
# or use the alias
vibe shadow rollback
```

---

## 📊 Observability

### Web Dashboard
Launch the visual trace explorer to inspect session logs and tool executions:
```bash
vibe dashboard start --port 8080
```

### Memory Status
View a summary of the tripartite memory system:
```bash
vibe memory status
```

---

## ⚙️ Configuration

Vibe Agent can be configured via `~/.vibe/config.yaml` or environment variables prefixed with `VIBE_`.

### Connecting to Local Ollama

Vibe Agent works seamlessly with local [Ollama](https://ollama.com/) instances. To use a local model, configure the following:

**Using Environment Variables:**
```bash
export VIBE_BASE_URL="http://localhost:11434"
export VIBE_MODEL="llama3"  # or your preferred model name
vibe
```

**Using `config.yaml`:**
```yaml
llm:
  base_url: "http://localhost:11434"
  default_model: "llama3"
```

*Note: Ensure Ollama is running (`ollama serve`) before starting Vibe Agent.*

### Configuration Reference
|---------|-------------|---------|
| `VIBE_MODEL` | Default LLM model name | `default` |
| `VIBE_BASE_URL` | LLM server base URL | `http://localhost:11434` |
| `LLM_API_KEY` | API key for the LLM provider | `None` |
| `VIBE_APPROVAL_MODE` | Tool execution security mode | `smart` |
| `VIBE_LOG_LEVEL` | Logging verbosity (DEBUG, INFO, etc) | `INFO` |

Example `config.yaml`:
```yaml
llm:
  default_model: "gpt-4o"
  base_url: "https://api.openai.com/v1"
  timeout: 120.0

security:
  approval_mode: "smart"
  file_safety:
    safe_root: "~/safe-zone"

memory:
  enabled: true
  wiki:
    auto_extract: true
```

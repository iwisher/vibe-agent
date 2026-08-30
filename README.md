# Vibe Agent

Vibe Agent is an open, visual-first interactive CLI agent harness. It is designed to provide a robust, resilient, and secure environment for LLM-based autonomous tasks, independent of any specific model or provider.

## 🚀 Key Features

- **Multi-Provider Fallback**: Seamlessly switch between OpenAI, Anthropic, Kimi, and other providers (via OpenRouter or Ollama) when primary models fail. Circuit breaker + latency-aware routing + cost tracking.
- **Streaming Response Support**: Real-time token streaming (`--stream` CLI flag) with native reasoning/thinking token display for Ollama, Anthropic, OpenAI, and OpenRouter.
- **Secure Tool Execution**: 5-layer security defense (pattern scanning, file safety, human approval, smart approver, checkpoints) with sandboxed Bash and jailed File tools.
- **Adaptive Dual-Tier Browser Tool**: Built-in browser tool (`browse` / `fetch_url`) featuring Tier 1 fast static HTTP extraction via Docling & stdlib HTML parser, and Tier 2 dynamic headless browser rendering via Playwright for JavaScript SPAs and element interactions (`click`). Includes strict SSRF protection against loopback/private/metadata networks and output truncation.
- **Context Management**: Automated compaction with 4 strategies (TRUNCATE, LLM_SUMMARIZE, OFFLOAD, DROP), plus adaptive iteration budgets based on task complexity.
- **Eval-Driven Development**: 50+ built-in eval cases, adversarial testing, multi-model scorecards, soak tests with degradation detection, and factory-per-case isolation.
- **Phase 2 Skill System**: Native vibe skill format with TOML frontmatter, validation, security scanning, atomic installation, typed variables, orchestration, marketplace, and dynamic tool declaration. **Deterministic script steps** let fixed logic live in bundled `scripts/` executed through the sandboxed Bash tool — the LLM only picks the skill and supplies typed inputs.
- **Skill-Maker (Self-Improving)**: Automatically detects recurring task patterns from wiki extractions, generates SKILL.md drafts via LLM, validates through sandbox, and proposes installation via approval gate. Validated lesson pages (principle-level, net-positive counters) are promoted into **script-backed executable skill drafts** that must pass a validator scan and a sandbox smoke-run before proposal.
- **Trajectory Reflection (Test-Time Learning)**: Post-session Reflector→Curator distills compact lessons (pitfalls / procedures / tips) from every run — including failures — gated by an LLM generality self-score, dedups them into lesson wiki pages with helpful/harmful counters that are updated by actual usage outcomes, and retrieves them into future prompts. Enabled by default.
- **Pivotal Error Retry**: When the same tool call fails repeatedly, the loop marks the pivotal turn and issues one bounded, reflection-guided retry of that call — reusing the correct prefix instead of re-planning the task. Security denials are never retried.
- **Tripartite Memory System**: Enabled by default. Query-time retrieval injects relevant wiki knowledge (confidence-gated, content snippets, contradiction-aware) plus "what worked before" snippets from similar successful past sessions into every prompt, on all planner tiers. Async knowledge extraction, FlashLLM contradiction detection, telemetry-triggered RLM analysis, vector search with sentence-transformers, wiki graph database, and per-tag novelty thresholds.
- **EvoX Meta-Evolution (Offline Pipeline)**: Self-improving offline search that jointly evolves candidate solutions and the search strategies used to generate them. Uses AdaEvolve-style multi-objective proxy scoring, UCB parent selection, and a lightweight strategy-code sandbox.
- **Shadow Workspace Rollbacks**: Auto-creates hidden git branch (`vibe/shadow-<session-id>`) before write-heavy operations. One-command restore if the session fails.
- **Multi-Agent Swarm**: DAG-based orchestration of specialized sub-agents (Research, Coding, Critic, Planner) with Pub/Sub message bus, broadcast deduplication, and shared wiki.
- **Multi-Agent Adversarial Red-Team**: Built-in 3-tier adversarial security testing framework (`vibe/redteam/` & `scripts/run_redteam.py`) testing 6 attack surfaces (S1 Bash patterns, S2 File jail, S3 SSRF, S4 SmartApprover prompt injection, S5 Skill supply chain, S7 MCP bridge). Validates offline defense layers, contains jailbroken mock LLMs (Tier B), and runs live model probes (Tier C).
- **React Trace Dashboard**: Web UI for session observability — timeline, wiki graph, telemetry charts, system stats. Dark theme, real-time WebSocket updates.
- **Preference Layer**: 8 persistent heuristics converting user feedback into agent behavior — tool defaults, approval rules, style, macros, recovery, compaction, provider routing, extraction.
- **Secret Redaction**: Comprehensive pattern stripping of API keys (OpenAI, AWS, GitHub, Slack, Google, Stripe, Discord, JWTs, private keys) and passwords from trace stores and logs.
- **Interactive CLI**: Markdown-rendered responses, structured tool-call panels (name, args, duration, truncated output), unified error panels, streaming with native reasoning/thinking display, persistent history, and rich skill/wiki/memory management commands.

---

## 🏗️ System Architecture

> [!TIP]
> **Interactive Version Available:** View the [Interactive System Architecture Diagram](docs/assets/system_architecture.html) directly in your browser to explore detailed component breakdowns, hover effects, and the complete tech stack.

The system is built on a modular **Harness** pattern. The **Query Loop State Machine** is the central orchestrator that connects the **Model Gateway** (for multi-provider LLM access), the **Tool Executor** (for secure sandboxed actions), and the **Tripartite Memory System** (for long-term knowledge persistence). New capabilities — **Skill-Maker**, **Shadow Workspace**, **Swarm Orchestration**, **Preference Layer**, and **EvoX** — all integrate through the same harness hooks.

**EvoX** operates as an offline pipeline stage. While the Query Loop handles live interactive sessions, EvoX runs longer, budgeted meta-evolution searches over tasks (prompts, programs, algorithms) and can feed discovered strategies or high-quality solutions back into the Skill System and Wiki memory.

```
User CLI / Dashboard
  │
  ▼
Query Loop State Machine (IDLE → PLANNING → TOOL_EXECUTION → SYNTHESIZING → COMPLETED)
  ├── Model Gateway ──► Providers (OpenRouter / Anthropic / Ollama / Kimi)
  │   ├── Circuit Breaker + Latency Router + Cost Tracker
  │   └── Fallback Chain (auto_fallback across providers)
  ├── Context Planner / Compactor (adaptive budgets)
  ├── Tool Executor ──► Bash, File & Browser (Jailed Sandbox & SSRF Defense)
  │                ──► Skill System + Skill-Maker Pipeline
  │                ──► Shadow Workspace (git branch backup)
  ├── Security Coordinator (5-layer defense)
  ├── Preference Layer (8 heuristics)
  ├── Swarm Orchestrator (multi-agent DAG)
  ├── Tripartite Memory System
  │    ├── LLMWiki + PageIndex + SharedDB (SQLite)
  │    ├── Knowledge Extractor (async background, incl. failed runs)
  │    ├── Trajectory Reflector (post-session lesson curation)
  │    ├── RLM Threshold Analyzer (telemetry-triggered LoRA training)
  │    └── WikiGraph + Semantic Deduplication
  └── EvoX Meta-Evolution (offline search)
       ├── Executable strategy code (parent / inspiration / operator selection)
       ├── Multi-objective proxy scoring + UCB exploration
       └── Discovered strategies → Skill System + Wiki memory
```

Read more in the [Architecture Document](docs/ARCHITECTURE.md).

---

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

# Enable self-improving skill generation
skill_maker:
  enabled: true
  min_pattern_frequency: 3
  confidence_threshold: 0.75

# Enable shadow workspace rollbacks
shadow_workspace:
  enabled: true
  auto_rollback: false
```

See the [Configuration Guide](docs/CONFIGURATION.md) for full details on setting up providers, multi-model fallback, tripartite memory, skill-maker, and shadow workspace.

---

## 🛠️ How to Run

### Prerequisites

- Python 3.11+
- An LLM provider (local Ollama, OpenRouter, Anthropic, etc.) — see [Configuration Guide](docs/CONFIGURATION.md)

### 1. Install

```bash
# Clone and install in editable mode (includes all dev extras)
git clone https://github.com/your-org/vibe-agent.git
cd vibe-agent
pip install -e ".[dev]"
```

### 2. Configure your LLM provider

```bash
# Copy the example config and edit it with your provider settings
cp docs/sample_config.yaml ~/.vibe/config.yaml
$EDITOR ~/.vibe/config.yaml
```

For a quick local setup with **Ollama** (no API key needed):

```bash
# Pull a model
ollama pull qwen3:8b

# Point Vibe at it
cat > ~/.vibe/config.yaml << 'EOF'
llm:
  default_model: "local"
  base_url: "http://localhost:11434"
  timeout: 120.0

models:
  local:
    provider: "ollama"
    model_id: "qwen3:8b"
EOF
```

### 3. Start the agent

```bash
# Interactive chat session
python -m vibe

# One-shot query
python -m vibe "Explain the difference between a mutex and a semaphore"

# With a specific model
python -m vibe --model qwen3:8b "What is the 52-week high of QQQ?"

# With real-time response streaming
python -m vibe --stream "Explain async/await in Python"

# With debug logging
python -m vibe --debug
```

### 4. Launch the dashboard

```bash
# Start the React trace dashboard (with auth token)
vibe dashboard start --port 8080

# Or start without authentication (dev mode)
vibe dashboard start --port 8080 --no-auth

# Then open http://localhost:8080 in your browser
```

### 5. Shadow workspace (safety net)

```bash
# Shadow is auto-created on write-heavy ops when enabled in config.
# If something goes wrong, restore the workspace:
vibe shadow restore <session-id>

# List available shadows
vibe shadow list

# Clean old shadows
vibe shadow clean --older-than-days 7
```

---

## 🧬 EvoX Meta-Evolution (Offline Pipeline)

EvoX is Vibe Agent's self-improving offline search component. It implements the two-level evolution process from the [EvoX paper](https://arxiv.org/pdf/2602.23413v1):

1. **Inner loop** evolves candidate solutions under an active search strategy.
2. **Outer loop** meta-evolves the search strategy itself when progress stagnates.

### Why it matters

Most LLM-driven optimizers use a fixed search strategy (e.g., always pick the best candidate and refine it). EvoX treats the strategy as an evolvable object: it can switch from greedy refinement to multi-objective pairing, to UCB-driven structural variation, and back to local polishing as the search landscape changes.

### Key capabilities

| Capability | What it does |
|---|---|
| Executable strategies | Strategies are Python code (`select_parent`, `select_inspiration`, `select_operator`) compiled in a lightweight sandbox. |
| Window-based stagnation | Monitors score improvement over a sliding window; triggers meta-evolution when `Δ` falls below a threshold. |
| Multi-objective proxy | AdaEvolve-style scalarization across `pareto_objectives` with per-objective `higher_is_better` directions. |
| UCB parent selection | Balances exploitation of high-scoring candidates with exploration of under-sampled ones. |
| Strategy database | Remembers deployed strategies and their score signals for score-biased parent selection. |

### Run EvoX

```bash
# String-match evolution
python -m vibe evox run --evaluator string --target "hello" --iterations 60

# Circle packing (paper-inspired benchmark)
python -m vibe evox run --evaluator circle_packing --target 12 --iterations 80

# Multi-objective signal-filter demo
python -m vibe evox run --evaluator signal_filter --iterations 50

# Traveling Salesman Problem (representative complex case)
python -m vibe evox run --evaluator tsp --target 10 --iterations 60

# Harness evolution: optimize the agent's own memory/reflection config knobs and
# extraction/reflection prompt variants against the built-in eval suite.
# Candidates are accepted only if they beat the same-run reference score without
# regressing >5% vs the baseline scorecard; every candidate is logged with full
# provenance (overrides, scores, eval report) — config is never auto-modified.
python -m vibe evox run --target harness --limit 20
```

### Integration with memory and skills

EvoX does not replace the live Query Loop; it runs **offline** and produces artifacts that feed back into the agent's long-term systems:

- **Skill System**: A successful evolved strategy can be captured as a reusable skill prompt or installed as a SKILL.md template. For example, a strategy that discovered effective prompt-rewriting patterns for a class of tasks can be promoted to a skill trigger.
- **Tripartite Memory / Wiki**: High-performing candidates and the final strategy trajectory are written to the wiki (via `vibe memory wiki`) so future sessions can retrieve them. The memory system can also suggest seed candidates from similar past tasks, warm-starting the next EvoX run.
- **Eval Store**: EvoX results are recorded in the eval store, contributing to the baseline scorecard and enabling regression detection.

Read the full implementation notes in [`docs/EvoX_implementation.md`](docs/EvoX_implementation.md).

---

## 📈 Example: QQQ Stock Analysis

Vibe Agent can be used as a general-purpose reasoning engine for tasks like financial data analysis. Below is an end-to-end example using the included `qqq_price.py` helper.

**1. Ask the agent to analyze QQQ directly:**

```bash
python -m vibe "Fetch the latest QQQ price, calculate its RSI(14) and MA(250), \
  and tell me if it is currently above or below its 200-day moving average."
```

**2. Run the included standalone QQQ price fetcher:**

```bash
# Requires: pip install yfinance
python qqq_price.py
```

Example output:
```
📊 QQQ Latest Data:
   Date: 2026-04-25 00:00:00-04:00
   Open:   $446.12
   High:   $452.80
   Low:    $443.91
   Close:  $450.67
   Volume: 42,871,200
```

**3. Full multi-ticker technical analysis (TSLA, MSFT, GOOGL, AMZN, NVDA):**

```bash
python stocks_analysis.py
```

This generates `stocks_analysis.png` with Price + MA250, RSI(14), and MACD charts for each ticker, and prints a summary table:

```
Ticker |      Close |      MA250 |    RSI |       MACD |     Signal
-----------------------------------------------------------------
TSLA   |    $245.67 |    $260.43 |  43.21 |      -3.45 |      -2.10
MSFT   |    $415.20 |    $392.88 |  58.70 |       4.12 |       3.80
...
```

**4. Teach the agent to remember your investment preferences using the Wiki:**

```bash
# After a conversation about QQQ strategy, check what Vibe extracted:
vibe memory status

# View extracted wiki pages
vibe memory wiki list --tag investing

# Search for related knowledge
vibe memory wiki search "QQQ moving average"
```

---

## 🧩 Skill System

Vibe Agent includes a native skill format designed for safe, portable, and versioned automation:

### SKILL.md Format
Skills are defined as markdown files with TOML frontmatter:

```markdown
+++
vibe_skill_version = "2.0.0"
id = "stock-analysis"
name = "Stock Analysis"
description = "Analyze stock prices from a local CSV or stooq.com and compute technical indicators"
category = "finance"
tags = ["stocks", "analysis", "finance"]

[trigger]
patterns = ["analyze stock", "check price of"]
required_tools = ["bash"]

[[variables]]
name = "ticker"
type = "string"
required = true
pattern = "^[A-Za-z0-9.-]{1,10}$"
description = "Stock ticker symbol, e.g. QQQ"

[[variables]]
name = "days"
type = "integer"
required = false
default = 30
minimum = 5
maximum = 3650

[[steps]]
id = "analyze"
description = "Run the deterministic analysis script and emit JSON indicators"
tool = "bash"
script = "scripts/analyze.py"
command = "{{ ticker }} --days {{ days }}"

[steps.verification]
exit_code = 0
json_has_keys = ["ticker", "sma_20"]
+++

# Stock Analysis Skill

## Overview
Fetches stock price data and computes basic technical indicators.
```

### Deterministic Script Steps

Anything deterministic belongs in a **script**, not in LLM-interpreted prose. A step
with `script = "scripts/analyze.py"` (relative to the skill directory, jailed under
`scripts/`) is executed by the runner itself: variable values are typed (string /
integer / pattern-validated), `shlex`-quoted, and appended to the script's argv, then
run through the sandboxed Bash tool — no shell, no unquoted metacharacters, no prompt
injection surface. The LLM only chooses *which* skill to run and *with what inputs*;
the step logic is fixed, reviewed, and install-time security-scanned code. Optional
`interpreter = "..."` overrides the default (`.py` → current Python, `.sh` → bash).

### Skill CLI Commands

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
vibe skill run stock-analysis ticker="QQQ"

# Uninstall a skill
vibe skill uninstall my-skill
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
| `SkillMakerPipeline` | **NEW** — Auto-detects recurring patterns, generates SKILL.md drafts, validates, proposes installation |

---

## 🧠 Memory Commands

```bash
# Show tripartite memory system status
vibe memory status

# List all wiki pages (filter by tag or status)
vibe memory wiki list
vibe memory wiki list --tag investing --status verified

# Search the wiki (BM25 full-text search + vector similarity)
vibe memory wiki search "QQQ moving average"

# Show a specific page
vibe memory wiki show <page-id-or-slug>

# Expire old draft pages
vibe memory wiki expire --days 30

# Compact accumulated lesson pages into principle-level pages (merge, never delete)
vibe memory wiki compact

# Rebuild the routing index
vibe memory wiki index rebuild
```

---

## 🎓 Trajectory Reflection (Test-Time Learning)

After every run — successful **or failed** — a Reflector→Curator pipeline distills what
the agent learned into compact, reusable lessons and stores them in the wiki:

- **Reflector**: reads the trajectory (query, tool calls, outcome) and produces up to
  `memory.reflection.max_lessons` lessons as `{title, lesson, applies_when, kind, generality}`
  where kind is `pitfall`, `procedure`, or `tip`. Trivial sessions are skipped.
- **Critique gate**: each lesson carries an LLM self-assessed `generality` score (1–5);
  lessons scoring below `min_generality` (default 3) are dropped before they ever reach
  the wiki, keeping instance-specific noise out of the playbook (missing scores fail open).
- **Curator**: deduplicates against existing lesson pages — a repeat lesson *merges*
  (additive refinement + helpful/harmful counters) instead of creating a duplicate,
  which keeps the playbook compact and prevents context collapse.
- **Usage feedback**: lessons actually injected into a run get their counters updated by
  that run's outcome (COMPLETED → helpful, ERROR → harmful), so the playbook self-ranks
  by demonstrated usefulness. When pivotal retry (below) marks a failure turn, new
  lessons are anchored on it.
- **Compaction**: `vibe memory wiki compact` clusters similar lesson pages and merges
  each cluster into one principle-level page (counters summed, citations unioned,
  originals archived — never deleted), preventing slow playbook bloat/collapse.
- **Promotion**: validated procedure lessons (generality ≥ 4, helpful − harmful ≥ 2) are
  compiled by the Skill-Maker into script-backed executable skill drafts — the
  lesson→capability compile step.
- **Retrieval**: lesson pages are indexed immediately, so future queries on related
  tasks retrieve them through the normal memory path (confidence-gated,
  contradiction-aware, with bounded content snippets) — no separate store needed.

Configured under `memory.reflection` in `~/.vibe/config.yaml` (enabled by default when
memory is on). Raw trajectories remain in the trace store for the offline RLM path;
curated lessons live in the wiki where they are actually retrieved.

### Pivotal error retry

When the same tool call fails twice with identical arguments, the loop marks that
iteration as the **pivotal turn** and — instead of drifting or degrading to ERROR —
issues one bounded, guided retry of just that call (error details attached, correct
prefix preserved, no re-planning). Security denials are never retried, the budget is
one retry per call signature, and everything degrades gracefully to prior behavior.
Configure under `error_recovery` (`pivotal_retry_enabled`, `max_pivotal_retries`).

---

## 🤖 Swarm Commands

```bash
# Run a task through the multi-agent swarm
vibe swarm "Research the latest React Server Components, then code a demo"

# List active swarm agents
vibe swarm status
```

---

## 🔬 Running Evaluations

```bash
# Run the built-in eval suite (50+ cases)
vibe eval run

# Filter by subsystem tag
vibe eval run --tag subsystem=memory

# Run a soak test
vibe eval soak --duration 30 --cpm 6

# Update the performance baseline
vibe eval update-baseline
```

---

## 🛡️ Multi-Agent Adversarial Red-Team

Vibe Agent includes a comprehensive, multi-tiered adversarial red-team engine (`vibe/redteam/`) to validate defenses against automated attacks, prompt injections, rogue agent tool executions, and long-horizon autonomy failures:

- **Tier A (Offline Component Attack Matrix)**: 30 deterministic test vectors across S1 (Bash pattern scanning & base64 pipe evasions), S2 (File safety & symlink jail traversal), S3 (Browser fetch SSRF), S4 (SmartApprover prompt injection fencing), S5 (Skill supply chain), and S7 (MCP bridge HTTP SSRF).
- **Tier B (Compromised-Model Containment)**: 7 scripted hostile-model jailbreak scenarios running through a real `QueryLoop` and `SecurityCoordinator` inside a strictly jailed environment.
- **Tier 3 (Long-Horizon Challenged Tasks)**: 10 complex multi-step scenarios addressing top failure modes in autonomous agent runtimes (cross-module refactoring AST preservation, stateful DB migration rollback, supply-chain package inspection, log error clustering, directory checksum sync, dynamic skill sandbox execution).
- **Tier C (Live-Model Gating)**: Live adversarial probe tests against reachable model endpoints (e.g. Google Gemini, Kimi).

### Built-in Tools & Deterministic Skills

- **`task_verifier` Built-in Tool**: High-reliability AST syntax and import parsing, cryptographic file checksum validation, SQLite schema/row invariants, and structured error signature log clustering.
- **`refactor-verifier` Skill**: Multi-file Python AST integrity and cross-import contract validation.
- **`db-migrator` Skill**: SQLite database schema migration with automatic snapshot backup, invariant check, and rollback.
- **`log-analyst` Skill**: Deep log triage clustering unique error signatures to eliminate context rot.
- **`dependency-auditor` Skill**: Supply-chain security scanning for unencrypted and obfuscated dependency declarations.

### Running Red-Team Tests

```bash
# Validate attack corpus definitions (6 suites, 30 entries)
python scripts/validate_redteam_corpus.py

# Run offline Tier A + Tier B + Tier 3 red-team suite
python scripts/run_redteam.py

# Run with live Gemini endpoint verification
export GEMINI_API_KEY="your-api-key"
python scripts/run_redteam.py --live --provider gemini --model gemini-flash-latest
```

All findings and regression results are compiled to [`docs/redteam_report.md`](docs/redteam_report.md) and [`docs/redteam_report.json`](docs/redteam_report.json). Read the architecture in the [Red-Team Plan](docs/plans/2026-08-29-multi-agent-redteam.md).

---

## 📖 Research Foundations

The memory, skill, and self-improvement designs follow published, peer-reviewed or
widely-cited work:

- [CodeAct: Executable Code Actions Elicit Better LLM Agents](https://arxiv.org/abs/2402.01030) (Wang et al., ICML 2024) — executable actions beat interpreted ones; basis for deterministic skill script steps.
- [Equipping Agents for the Real World with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) (Anthropic, 2025) — deterministic logic lives in `scripts/`, not prose; progressive disclosure.
- [Agent Workflow Memory (AWM)](https://arxiv.org/abs/2409.07429) (Wang et al., ICML 2025) — induce reusable workflows from past trajectories and inject them at inference.
- [Agentic Context Engineering (ACE)](https://arxiv.org/abs/2510.04618) (Zhang et al., 2025) — context as an evolving playbook; incremental delta lessons with helpful/harmful counters; merge-don't-rewrite curation.
- [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) (Zhao et al., AAAI 2024) — distill cross-task insights from successes *and* failures.
- [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) (Shinn et al., NeurIPS 2023) — failed trajectories are the richest learning signal.
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110) (Xu et al., NeurIPS 2025) — structured, status-aware memory notes.
- [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442) (Park et al., 2023) — relevance/recency/importance gating at retrieval time.

The 2026 frontier (compile-don't-retrieve, feedback loops, harness optimization) informs
the current and planned increments — full study log with all sources in
[`docs/plans/2026-08-22-experience-learning-study-and-plan.md`](docs/plans/2026-08-22-experience-learning-study-and-plan.md):

- [XSkill: Continual Learning from Experience and Skills in Multimodal Agents](https://arxiv.org/abs/2603.12056) (ICML 2026) — critique at write time + usage-history feedback loop; basis for the generality gate and usage-updated counters.
- [Rethinking Continual Experience Internalization](https://arxiv.org/abs/2606.04703) (2026) — naive experience accumulation collapses; principle-level lessons survive; motivates the generality gate and planned lesson compaction.
- [PivoARL: Agent RL via Pivotal-Aware Self-Feedback Retry](https://arxiv.org/abs/2607.03702) (2026) — retry locally from the pivotal error, reuse the correct prefix; basis for pivotal error retry.
- [Evo-Harness: Context-to-Harness Skill Compilation](https://arxiv.org/abs/2608.15071) (2026) — compile one-shot executions into executable skill harnesses; guides lesson→skill promotion.
- [Muscle Memory for Agents: Compile not Merely Retrieve](https://arxiv.org/abs/2608.08995) (2026) — compilation beats retrieval for recurring intent; guides lesson→skill promotion.
- [Meta-Harness: End-to-End Optimization of Model Harnesses](https://arxiv.org/abs/2603.28052) (Lee et al., 2026) — the harness itself is the optimizable object, searched with full access to prior candidates' source/scores/traces; guides EvoX-over-harness.
- [Harness Updating Is Not Harness Benefit](https://arxiv.org/abs/2605.30621) (2026) — self-modifications require held-out eval acceptance; basis for the regression gate on harness changes.

---

## 📚 Documentation Index

- [Architecture](docs/ARCHITECTURE.md)
- [Configuration Guide](docs/CONFIGURATION.md)
- [Roadmap & Plans](docs/ROADMAP.md)
- [Evaluation Suite](docs/EVALUATION.md)
- [Changelog](docs/CHANGELOG.md)

---

*Vibe Agent is currently in Phase 4.2 (Self-Improving Skill-Maker) + Phase 5.2 (Shadow Workspace). See the [Roadmap](docs/ROADMAP.md) for what's next. Test suite: **1,937+ tests passing**.*

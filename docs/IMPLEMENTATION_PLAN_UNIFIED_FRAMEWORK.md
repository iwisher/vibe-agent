# Implementation Plan: Unified Agentic Workflow Framework

**Reference:** [Unified Agentic Workflow Design Document](./UNIFIED_AGENT_FRAMEWORK.md)  
**Date:** 2026-04-26  
**Owner:** vibe-agent Team

---

## Phase 1: Skill System Standardization (Level 1-3)
**Goal:** Align the current skill system with the `agentskills.io` Open Standard.

### Tasks
- [ ] **1.1 Migration to `SKILL.md` v2.0:**
    - Update `vibe/harness/skills/parser.py` to support full YAML frontmatter and Markdown body partitioning.
    - Implement "Level 1" metadata extraction for the `HybridPlanner`.
- [ ] **1.2 Resource Loader (Level 3):**
    - Implement a safe loader for Python/Bash scripts bundled within a skill directory.
    - Path: `vibe/harness/skills/loader.py`
- [ ] **1.3 Variable Substitution Hardening:**
    - Move from naive string replace to `string.Template` or `jinja2` (sandboxed) for environment variable injection in skill scripts.

---

## Phase 2: Stitch Visual Bridge (MCP)
**Goal:** Enable the agent to see and interpret visual design artifacts.

### Tasks
- [ ] **2.1 `StitchBridge` Implementation:**
    - Extend `vibe/tools/mcp_bridge.py` to support long-running MCP server connections for Stitch.
    - Implement `get_design_tokens()` and `get_component_hierarchy()` methods.
- [ ] **2.2 Design-to-Task Compiler:**
    - Create a utility that converts Stitch MCP output into a structured `DESIGN.md`.
    - Implement a "Visual Validator" tool that uses design tokens to check implementation (e.g., CSS variable compliance).

---

## Phase 3: Autonomous VM Sandbox (Manus-style)
**Goal:** Provide a secure, isolated execution environment for autonomous tasks.

### Tasks
- [ ] **3.1 Docker-based Sandbox Manager:**
    - Implement a `SandboxManager` that spawns ephemeral Docker containers (Ubuntu-based) for executing "Level 3" skill resources.
    - Support for networking constraints (block egress except for approved domains).
- [ ] **3.2 Tool Delegation to Sandbox:**
    - Update `BashTool` to execute within the sandbox if `security.backend = "docker"` is configured.
    - Implement file sync between the host workspace and the sandbox container.

---

## Phase 4: Contextual Intent & MCTS (AFLOW)
**Goal:** Improve long-term reasoning and prevent goal drift.

### Tasks
- [ ] **4.1 STITCH Memory Implementation:**
    - Update `QueryLoop` to maintain a persistent `intent_stack`.
    - Implement the "todo.md Recitation" step at the start of each iteration processing phase.
- [ ] **4.2 MCTS-based Workflow Planner:**
    - Integrate a lightweight Monte Carlo Tree Search (MCTS) in `HybridPlanner` to simulate potential tool-call sequences before execution.
    - Evaluate "branches" using a reward function based on design fidelity and test passing.

---

## Phase 5: Verification & Evaluation
**Goal:** Ensure the framework delivers production-grade results.

### Tasks
- [ ] **5.1 Design Fidelity Evals:**
    - Add 10 new eval cases to `vibe/evals/builtin/` that specifically test Stitch-to-React conversion.
- [ ] **5.2 Long-Turn Stability Soak Test:**
    - Implement a soak test that runs 50+ iterations of a complex intent (e.g., "Build a full glassmorphic dashboard").

---

## Milestones & Timeline

| Milestone | Description | Est. Effort |
|-----------|-------------|-------------|
| **M1: Skill Standard** | Full `agentskills.io` compatibility | 3 Days |
| **M2: Visual Bridge** | Stitch MCP integration + `DESIGN.md` | 5 Days |
| **M3: Sandbox** | Docker-based isolation + Bash delegation | 4 Days |
| **M4: Intent Engine** | STITCH memory + todo.md recitation | 3 Days |
| **M5: Release 1.0** | End-to-end autonomous visual-to-code workflow | 2 Days |

**Total Estimated Duration:** ~17 Days

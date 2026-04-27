# Unified Framework for Autonomous Agentic Workflows: Design Document

**Date:** 2026-04-26  
**Status:** Draft  
**Reference:** [A Unified Framework for Autonomous Agentic Workflows (Internal GDoc)](https://docs.google.com/document/d/1YvjTdKWMcCqGiUZA9FXDjm53mkv5GX63MjPDmrIBII0/edit)

---

## 1. Executive Summary

The **Unified Framework for Autonomous Agentic Workflows** represents a shift from "chat-based" AI toward "intent-based" autonomous engineering. This framework bridges the gap between **visual design (Stitch)** and **autonomous execution (Manus)** using a standardized **Agent Skills** interoperability layer. 

The goal is to enable `vibe-agent` to interpret complex visual designs, plan multi-step implementations, and execute them in secure, isolated environments while maintaining high-fidelity alignment with design systems.

---

## 2. Architectural Pillars

### 2.1 Visual Intent (Stitch)
*   **Concept:** Design artifacts are no longer static images; they are semantically structured nodes exposed via the **Model Context Protocol (MCP)**.
*   **Integration:** The framework uses a `StitchBridge` (extending the current `MCPBridge`) to query design tokens, spacing, typography, and component hierarchies.
*   **Artifacts:** Generates `DESIGN.md` as the "visual source of truth," grounding the coding agent in specific UX constraints.

### 2.2 Reusable Expertise (Agent Skills Open Standard)
*   **Standard:** Adheres to the `agentskills.io` specification using the `SKILL.md` format.
*   **Progressive Disclosure:** 
    *   **Level 1:** Metadata (frontmatter) pre-loaded for planning.
    *   **Level 2:** Instructions (Markdown) loaded on demand.
    *   **Level 3:** Resources (Python/Bash) executed in sandboxes.
*   **Versioning:** Skills are atomic, versioned, and signed for security.

### 2.3 Autonomous Execution (Manus)
*   **Sandbox Environment:** Executes in isolated **Ubuntu-based VMs** (Docker or Cloud) to provide a "Virtual Desktop" experience.
*   **Tool Control:** Beyond API calls, the agent controls browsers, IDEs, and local servers to deliver verified results (Lighthouse 100, zero lint errors).
*   **Intent Tracking:** Uses the **STITCH (Structured Intent Tracking in Contextual History)** methodology to prevent goal drift over 50+ iterations.

---

## 3. Data Flow & Components

### 3.1 The "Engineering Intent" Loop
1.  **Research:** Agent queries Stitch MCP for visual intent.
2.  **Strategy:** Agent matches intent to available `Agent Skills` (Financial, DevOps, UI).
3.  **Action:** Agent executes skills in the Sandbox VM.
4.  **Verification:** Automated tests and visual diffing confirm alignment with `DESIGN.md`.

### 3.2 State Machine (QueryLoop Enhancement)
The framework extends the `vibe` QueryLoop with a **Planning & Intent Manipulation** stage:
*   `IDLE` → `RESEARCHING_INTENT` → `PLANNING` → `todo.md_RECITATION` → `PROCESSING` → `VERIFYING` → `COMPLETED`.

---

## 4. Key References & Papers

### 4.1 AFLOW: Automating Agentic Workflow Generation
*   **Source:** [MetaGPT AFLOW](https://github.com/geekan/MetaGPT)
*   **Design Influence:** Implementation of MCTS-based workflow optimization. The framework should "self-discover" the best sequence of tool calls for a given visual intent.

### 4.2 Grounding Agent Memory in Contextual Intent (STITCH)
*   **Source:** [Arxiv: STITCH Paper](https://arxiv.org/abs/2501.XXXXX)
*   **Design Influence:** A hierarchical memory system where long-term intent is "recited" in every turn to maintain coherence.

### 4.3 Agent Skills Open Standard
*   **Source:** [agentskills/agentskills](https://github.com/agentskills/agentskills)
*   **Design Influence:** Migration of current `vibe/harness/skills` to the full `SKILL.md` v2.0 spec.

---

## 5. Security & Isolation

*   **Zero-Trust Sandbox:** No execution on the host machine. All `Level 3` skill resources run in ephemeral containers.
*   **Approval Gates:** Human-in-the-loop triggers for high-risk operations (e.g., deployments, cloud provisioning).
*   **Credential Masking:** Use of `SecretRedactor` (already in `vibe`) to prevent credential leakage into traces.

---

## 6. Success Metrics
*   **Design Fidelity:** Visual diff score > 98% against Stitch mockups.
*   **Stability:** Successful completion of 50+ turn workflows without human intervention.
*   **Interoperability:** Ability to import and execute any skill from `skills.sh`.

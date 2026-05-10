# Phase 3 Completion Plan: Preference Layer + Heuristic Learning Integration

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task. Load phase-gated-code-review skill for review workflow.

**Goal:** Implement the "Preference Layer" — a system that converts user feedback into persistent, testable, code-based heuristics. This is the bridge from "agent uses tools" to "agent maintains itself" per Jiayi Weng's Heuristic Learning paradigm. Covers 8 preference types beyond skills.

**Architecture:** A unified `PreferenceRegistry` with typed policy objects, pluggable miners for each feedback source, and hooks injected at existing coordinator boundaries (ToolExecutor, CompactionCoordinator, ApprovalGate, etc.). All preferences are default-disabled, opt-in via config.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite (preference store), Jinja2 (macro templates), existing vibe-agent infrastructure.

**Test Baseline:** 983 passing (ignoring known-broken config tests + dashboard test), as of 2026-05-09.

---

## Current State Analysis

### Already Implemented

| Component | Status | Location |
|-----------|--------|----------|
| Skill system (create/validate/install/run) | ✅ Done | `vibe/harness/skills/` |
| Tripartite Memory (LLMWiki + PageIndex + Telemetry) | ✅ Done | `vibe/memory/` |
| ToolExecutor with HookPipeline | ✅ Done | `vibe/core/coordinators.py`, `vibe/harness/constraints.py` |
| CompactionCoordinator (4 strategies) | ✅ Done | `vibe/core/coordinators.py` |
| ApprovalGate protocol (manual/smart/auto) | ✅ Done | `vibe/harness/security/` |
| ErrorRecovery with retry | ✅ Done | `vibe/core/error_recovery.py` |
| CostRouter (standalone) | ✅ Done | `vibe/core/cost_router.py` |
| ContextPlanner (standalone) | ✅ Done | `vibe/core/context_planner.py` |
| DAGPlanner + DAGExecutor (standalone) | ✅ Done | `vibe/harness/dag_planner.py` |
| SessionStore + resume | ✅ Done | `vibe/harness/memory/session_store.py` |
| Config system (Pydantic v2) | ✅ Done | `vibe/core/config.py` |
| Eval suite with baseline scorecard | ✅ Done | `run_e2e_evals.py`, `tests/` |

### Still Missing / Needs Completion

| Workstream | Missing Pieces |
|------------|---------------|
| **P1: Tool Preferences** | No files. Needs `ToolPreferenceRegistry`, hook in `ToolExecutor`, CLI commands, tests. |
| **P2: Approval Rules** | No files. Needs `ApprovalPolicyDB`, miner from approval history, hook in `AutoApproveGate`, tests. |
| **P3: Response Style Policy** | No files. Needs `ResponseStylePolicy`, system prompt injection, user feedback miner, tests. |
| **P4: Macro Sessions** | No files. Needs `MacroSession` DAG runner, YAML template engine, trigger system (cron/CLI), tests. |
| **P5: Recovery Rules** | No files. Needs `RecoveryRuleDB`, hook in `ErrorRecovery`, miner from session logs, tests. |
| **P6: Compaction Policy** | No files. Needs `CompactionPolicy`, hook in `CompactionCoordinator`, user feedback miner, tests. |
| **P7: Provider Preference Matrix** | No files. Needs `ProviderPreferenceMatrix`, hook in `CostRouter`, miner from model override history, tests. |
| **P8: Extraction Policy** | No files. Needs `ExtractionPolicy`, hook in `KnowledgeExtractor`, miner from wiki edits, tests. |

---

## Phase Execution Order

```
Phase A: P1 Tool Preferences (foundation — simplest, establishes registry pattern)
    → Gemini CLI review → fix → PASS
Phase B: P3 Response Style Policy (easy, high UX impact, validates registry pattern)
    → Gemini CLI review → fix → PASS
Phase C: P2 Approval Rules (safety-critical, builds on P1 registry pattern)
    → Gemini CLI review → fix → PASS
Phase D: P4 Macro Sessions (unlocks power-user workflows, reuses DAGPlanner)
    → Gemini CLI review → fix → PASS
Phase E: P5 Recovery Rules + P6 Compaction Policy (parallel, independent)
    → Gemini CLI review → fix → PASS
Phase F: P7 Provider Preference + P8 Extraction Policy (parallel, independent)
    → Gemini CLI review → fix → PASS
Phase G: Integration tests + full suite regression + bulk Gemini CLI review
    → fix → PASS
```

**Parallelization opportunity:** While coding Phase D, run Gemini review for Phase C in background. While coding Phase F, run Gemini review for Phase E in background.

---

# ─────────────────────────────────────────
# PHASE A: P1 Tool Preferences
# ─────────────────────────────────────────

## Overview

Capture user tool argument overrides and persist as default templates. When user says "always use `git diff --stat`", the next `git diff` tool call automatically gets `--stat` appended. Applied at `ToolExecutor` PRE_MODIFY stage.

## Files

| File | Action |
|------|--------|
| `vibe/preferences/__init__.py` | **NEW** — package init |
| `vibe/preferences/registry.py` | **NEW** — `PreferenceRegistry` base class |
| `vibe/preferences/tool_prefs.py` | **NEW** — `ToolPreferenceRegistry` |
| `vibe/preferences/models.py` | **NEW** — shared Pydantic models |
| `vibe/core/coordinators.py` | Modify — hook tool_prefs into `ToolExecutor` |
| `vibe/cli/main.py` | Modify — add `vibe pref tool` subcommands |
| `tests/preferences/test_tool_prefs.py` | **NEW** |
| `tests/preferences/test_registry.py` | **NEW** |

## Task A1: Create preference package structure

**Objective:** Set up `vibe/preferences/` package with base registry and shared models.

**Step 1: Create `vibe/preferences/__init__.py`**

```python
"""Preference Layer for vibe-agent.

Converts user feedback into persistent, testable, code-based heuristics.
"""

from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.models import PreferencePolicy, PreferenceRule

__all__ = ["PreferenceRegistry", "PreferencePolicy", "PreferenceRule"]
```

**Step 2: Create `vibe/preferences/models.py`**

```python
"""Shared Pydantic models for preference layer."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PreferenceSource(str, Enum):
    """How the preference was created."""
    EXPLICIT = "explicit"      # user ran `vibe pref set ...`
    INFERRED = "inferred"      # mined from session history
    IMPORTED = "imported"      # from skill or config


class PreferenceRule(BaseModel):
    """A single preference rule."""
    
    model_config = {"extra": "ignore"}  # Forward-compatible with new fields
    
    rule_id: str = Field(default_factory=lambda: f"rule_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}")
    pattern: str              # regex, glob, or exact match
    action: str               # what to do (tool-specific)
    action_args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0     # 0.0-1.0, reserved for future ML scoring
    source: PreferenceSource = PreferenceSource.EXPLICIT
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used_at: str | None = None  # For stale rule pruning
    hit_count: int = 0        # how many times applied (batched, not per-hit)
    enabled: bool = True


class PreferencePolicy(BaseModel):
    """A collection of rules for a specific preference domain."""
    
    model_config = {"extra": "ignore"}
    
    domain: str               # e.g., "tools", "approval", "style"
    rules: list[PreferenceRule] = Field(default_factory=list)
    enabled: bool = True
    
    def add_rule(self, rule: PreferenceRule) -> None:
        self.rules.append(rule)
    
    def get_enabled_rules(self) -> list[PreferenceRule]:
        return [r for r in self.rules if r.enabled]
    
    def remove_rule(self, rule_id: str) -> bool:
        for i, r in enumerate(self.rules):
            if r.rule_id == rule_id:
                self.rules.pop(i)
                return True
        return False
```

**Step 3: Create `vibe/preferences/registry.py`**

```python
"""Base preference registry with SQLite persistence."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule


class PreferenceRegistry:
    """SQLite-backed registry for preference policies.
    
    Each domain gets its own policy table. Rules are JSON-serialized.
    
    NOTE: hit_count updates are batched in memory and flushed on session shutdown
    to avoid race conditions during parallel tool execution.
    """
    
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                db_path = str(Path(base) / "preferences.db")
            else:
                db_path = str(Path.home() / ".vibe" / "memory" / "preferences.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._pending_hit_counts: dict[str, dict[str, int]] = {}  # domain -> {rule_id: count}
    
    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS preference_policies (
                    domain TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    updated_at TEXT
                );
            """)
    
    def save_policy(self, policy: PreferencePolicy) -> None:
        """Save or update a policy."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO preference_policies 
                   (domain, policy_json, enabled, updated_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (policy.domain, json.dumps(policy.model_dump()), int(policy.enabled)),
            )
    
    def load_policy(self, domain: str) -> PreferencePolicy | None:
        """Load a policy by domain. Returns None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT policy_json FROM preference_policies WHERE domain = ?",
                (domain,),
            ).fetchone()
            if row is None:
                return None
            return PreferencePolicy(**json.loads(row[0]))
    
    def list_domains(self) -> list[str]:
        """List all domains with saved policies."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT domain FROM preference_policies WHERE enabled = 1"
            ).fetchall()
            return [r[0] for r in rows]
    
    def delete_policy(self, domain: str) -> bool:
        """Delete a policy. Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM preference_policies WHERE domain = ?",
                (domain,),
            )
            return cursor.rowcount > 0
    
    def batch_hit(self, domain: str, rule_id: str) -> None:
        """Record a hit in memory (not persisted yet)."""
        if domain not in self._pending_hit_counts:
            self._pending_hit_counts[domain] = {}
        self._pending_hit_counts[domain][rule_id] = self._pending_hit_counts[domain].get(rule_id, 0) + 1
    
    def flush_hits(self) -> None:
        """Persist all pending hit counts to the database. Call on session shutdown."""
        if not self._pending_hit_counts:
            return
        
        for domain, hits in self._pending_hit_counts.items():
            policy = self.load_policy(domain)
            if policy is None:
                continue
            
            for rule in policy.rules:
                if rule.rule_id in hits:
                    rule.hit_count += hits[rule.rule_id]
                    rule.last_used_at = datetime.now(timezone.utc).isoformat()
            
            self.save_policy(policy)
        
        self._pending_hit_counts.clear()
    
    def prune_stale(self, days: int = 30) -> int:
        """Remove inferred rules not used in N days. Returns count removed."""
        from datetime import datetime, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        removed = 0
        
        for domain in self.list_domains():
            policy = self.load_policy(domain)
            if policy is None:
                continue
            
            original_len = len(policy.rules)
            policy.rules = [
                r for r in policy.rules
                if r.source != PreferenceSource.INFERRED
                or (r.last_used_at is not None and r.last_used_at > cutoff)
                or r.hit_count == 0  # Never used inferred rules are also stale
            ]
            removed += original_len - len(policy.rules)
            self.save_policy(policy)
        
        return removed
```

**Step 4: Run tests to verify base registry**

Create `tests/preferences/test_registry.py`:

```python
import tempfile
from pathlib import Path

from vibe.preferences.registry import PreferenceRegistry
from vibe.preferences.models import PreferencePolicy, PreferenceRule


class TestPreferenceRegistry:
    def test_save_and_load_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))
            
            policy = PreferencePolicy(domain="test")
            policy.add_rule(PreferenceRule(pattern="git", action="append_args", action_args={"args": ["--stat"]}))
            reg.save_policy(policy)
            
            loaded = reg.load_policy("test")
            assert loaded is not None
            assert loaded.domain == "test"
            assert len(loaded.rules) == 1
            assert loaded.rules[0].pattern == "git"
    
    def test_list_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))
            reg.save_policy(PreferencePolicy(domain="tools"))
            reg.save_policy(PreferencePolicy(domain="style"))
            assert sorted(reg.list_domains()) == ["style", "tools"]
    
    def test_delete_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = PreferenceRegistry(str(db))
            reg.save_policy(PreferencePolicy(domain="tools"))
            assert reg.delete_policy("tools")
            assert reg.load_policy("tools") is None
```

Run: `pytest tests/preferences/test_registry.py -v`
Expected: 3 passed

**Step 5: Commit**

```bash
git add vibe/preferences/ tests/preferences/
git commit -m "feat(preferences): add base registry and models"
```

---

## Task A2: Implement ToolPreferenceRegistry

**Objective:** Create tool-specific preference registry with argument override logic.

**Step 1: Create `vibe/preferences/tool_prefs.py`**

```python
"""Tool preference registry — default argument overrides for tool calls."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry

logger = logging.getLogger(__name__)


class ToolPreferenceRegistry:
    """Registry for tool argument preferences.
    
    Maps tool_name → default args that are merged into every tool call.
    """
    
    DOMAIN = "tools"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def set_default_args(self, tool_name: str, args: dict[str, Any], source: PreferenceSource = PreferenceSource.EXPLICIT) -> PreferenceRule:
        """Set default arguments for a tool.
        
        Args:
            tool_name: Exact tool name or glob pattern (e.g., "git_*")
            args: Dict of argument name → default value
            source: How this preference was created
        """
        rule = PreferenceRule(
            pattern=tool_name,
            action="merge_args",
            action_args={"args": args},
            source=source,
        )
        # Remove existing rule for same pattern
        if self._policy:
            self._policy.rules = [r for r in self._policy.rules if r.pattern != tool_name]
            self._policy.add_rule(rule)
            self._save()
        return rule
    
    def remove_default_args(self, tool_name: str) -> bool:
        """Remove default args for a tool. Returns True if removed."""
        if self._policy is None:
            return False
        original_len = len(self._policy.rules)
        self._policy.rules = [r for r in self._policy.rules if r.pattern != tool_name]
        if len(self._policy.rules) < original_len:
            self._save()
            return True
        return False
    
    def apply(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Apply matching preferences to tool arguments.
        
        Returns a new dict with defaults merged in (user args take precedence).
        Hit counts are batched in memory and flushed on session shutdown.
        """
        if self._policy is None or not self._policy.enabled:
            return arguments
        
        result = dict(arguments)
        for rule in self._policy.get_enabled_rules():
            if self._matches(rule.pattern, tool_name):
                # Batch hit count in registry (not persisted yet)
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                if rule.action == "merge_args":
                    defaults = rule.action_args.get("args", {})
                    # Defaults only apply if key not already present
                    for key, val in defaults.items():
                        if key not in result:
                            result[key] = val
                elif rule.action == "append_args":
                    # For list-valued args, append defaults
                    for key, val in rule.action_args.get("args", {}).items():
                        if key not in result:
                            result[key] = val
        
        return result
    
    def list_preferences(self) -> list[PreferenceRule]:
        """List all tool preferences."""
        if self._policy is None:
            return []
        return list(self._policy.rules)
    
    @staticmethod
    def _matches(pattern: str, tool_name: str) -> bool:
        """Check if pattern matches tool_name (exact or glob)."""
        if pattern == tool_name:
            return True
        return fnmatch.fnmatch(tool_name, pattern)
```

**Step 2: Create `tests/preferences/test_tool_prefs.py`**

```python
import tempfile
from pathlib import Path

from vibe.preferences.tool_prefs import ToolPreferenceRegistry
from vibe.preferences.registry import PreferenceRegistry


class TestToolPreferenceRegistry:
    def test_set_and_apply_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            
            reg.set_default_args("git_diff", {"flags": ["--stat"]})
            result = reg.apply("git_diff", {"file": "README.md"})
            
            assert result["file"] == "README.md"
            assert result["flags"] == ["--stat"]
    
    def test_user_args_take_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            
            reg.set_default_args("pytest", {"flags": ["-x"]})
            result = reg.apply("pytest", {"flags": ["-v"]})
            
            # User-provided -v should not be overwritten
            assert result["flags"] == ["-v"]
    
    def test_glob_pattern_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            
            reg.set_default_args("git_*", {"cwd": "."})
            result = reg.apply("git_status", {})
            assert result["cwd"] == "."
    
    def test_remove_preference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            
            reg.set_default_args("test_tool", {"arg": "val"})
            assert reg.remove_default_args("test_tool")
            result = reg.apply("test_tool", {})
            assert "arg" not in result
    
    def test_hit_count_tracking(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            reg = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            
            reg.set_default_args("counter_tool", {"count": 0})
            reg.apply("counter_tool", {})
            reg.apply("counter_tool", {})
            
            # Hit counts are batched in memory — flush to persist
            reg._registry.flush_hits()
            
            # Reload to verify persistence
            reg2 = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            rules = reg2.list_preferences()
            assert rules[0].hit_count == 2
```

Run: `pytest tests/preferences/test_tool_prefs.py -v`
Expected: 5 passed

**Step 3: Commit**

```bash
git add vibe/preferences/tool_prefs.py tests/preferences/test_tool_prefs.py
git commit -m "feat(preferences): add ToolPreferenceRegistry with arg overrides"
```

---

## Task A3: Wire ToolPreferenceRegistry into ToolExecutor

**Objective:** Apply tool preferences at `ToolExecutor` PRE_MODIFY stage.

**Step 1: Modify `vibe/core/coordinators.py`**

Find `ToolExecutor` class. In `__init__`, add optional `tool_prefs` parameter:

```python
class ToolExecutor:
    def __init__(
        self,
        tool_system: ToolSystem,
        hook_pipeline: HookPipeline | None = None,
        mcp_bridge: MCPBridge | None = None,
        tool_prefs: ToolPreferenceRegistry | None = None,  # NEW
    ):
        self.tool_system = tool_system
        self.hook_pipeline = hook_pipeline or HookPipeline()
        self.mcp_bridge = mcp_bridge
        self.tool_prefs = tool_prefs  # NEW
```

In the `execute` method (or wherever tool calls are prepared), before calling the tool, apply preferences:

```python
# Inside execute(), before tool invocation:
if self.tool_prefs is not None:
    tool_name = extract_tool_call_name(tool_call)
    original_args = extract_tool_call_arguments(tool_call)
    merged_args = self.tool_prefs.apply(tool_name, original_args)
    if merged_args != original_args:
        # Update the tool_call dict with merged args
        tool_call = self._update_tool_call_args(tool_call, merged_args)
```

Add helper:

```python
def _update_tool_call_args(self, tool_call: dict, new_args: dict) -> dict:
    """Return a copy of tool_call with updated arguments."""
    updated = dict(tool_call)
    if "function" in updated:
        updated["function"] = dict(updated["function"])
        if isinstance(updated["function"].get("arguments"), dict):
            updated["function"]["arguments"] = new_args
        elif isinstance(updated["function"].get("arguments"), str):
            import json
            updated["function"]["arguments"] = json.dumps(new_args)
    else:
        updated["arguments"] = new_args
    return updated
```

**Step 2: Wire in `QueryLoopFactory`**

In `vibe/core/query_loop_factory.py`, find where `ToolExecutor` is instantiated. Add:

```python
from vibe.preferences.tool_prefs import ToolPreferenceRegistry

# In factory method:
tool_prefs = ToolPreferenceRegistry() if config.preferences.tools_enabled else None
tool_executor = ToolExecutor(
    tool_system=tool_system,
    tool_prefs=tool_prefs,  # NEW
)
```

**Step 3: Add config field**

In `vibe/core/config.py`, add to `VibeConfig` or appropriate sub-config:

```python
class PreferenceConfig(BaseModel):
    """Preference layer configuration."""
    enabled: bool = False  # Master switch
    tools_enabled: bool = True
    approval_enabled: bool = True
    style_enabled: bool = True
    macros_enabled: bool = True
    recovery_enabled: bool = True
    compaction_enabled: bool = True
    provider_enabled: bool = True
    extraction_enabled: bool = True
```

**Step 4: Add CLI commands**

In `vibe/cli/main.py`, add under a new `pref_app`:

```python
pref_app = typer.Typer(help="Preference management")
app.add_typer(pref_app, name="pref")

@pref_app.command("tool-set")
def pref_tool_set(
    tool_name: str = typer.Argument(..., help="Tool name or glob pattern"),
    args: str = typer.Argument(..., help="JSON dict of default args"),
):
    """Set default arguments for a tool."""
    from vibe.preferences.tool_prefs import ToolPreferenceRegistry
    import json
    
    registry = ToolPreferenceRegistry()
    parsed = json.loads(args)
    rule = registry.set_default_args(tool_name, parsed)
    console.print(f"[green]✓[/green] Set defaults for [bold]{tool_name}[/bold]: {parsed}")
    console.print(f"[dim]Rule ID: {rule.rule_id}[/dim]")

@pref_app.command("tool-list")
def pref_tool_list():
    """List all tool preferences."""
    from vibe.preferences.tool_prefs import ToolPreferenceRegistry
    
    registry = ToolPreferenceRegistry()
    rules = registry.list_preferences()
    if not rules:
        console.print("[dim]No tool preferences set.[/dim]")
        return
    
    table = Table(title="Tool Preferences")
    table.add_column("Pattern", style="cyan")
    table.add_column("Action", style="green")
    table.add_column("Args", style="dim")
    table.add_column("Hits", style="yellow")
    
    for r in rules:
        table.add_row(r.pattern, r.action, str(r.action_args), str(r.hit_count))
    console.print(table)

@pref_app.command("tool-remove")
def pref_tool_remove(
    tool_name: str = typer.Argument(..., help="Tool name pattern to remove"),
):
    """Remove default arguments for a tool."""
    from vibe.preferences.tool_prefs import ToolPreferenceRegistry
    
    registry = ToolPreferenceRegistry()
    if registry.remove_default_args(tool_name):
        console.print(f"[green]✓[/green] Removed preferences for [bold]{tool_name}[/bold]")
    else:
        console.print(f"[yellow]No preferences found for {tool_name}[/yellow]")
```

**Step 5: Add integration test**

Create `tests/preferences/test_integration.py`:

```python
import tempfile
from pathlib import Path

from vibe.core.coordinators import ToolExecutor
from vibe.preferences.tool_prefs import ToolPreferenceRegistry
from vibe.preferences.registry import PreferenceRegistry
from vibe.tools.tool_system import ToolSystem


class TestToolPreferenceIntegration:
    def test_tool_executor_applies_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            prefs = ToolPreferenceRegistry(PreferenceRegistry(str(db)))
            prefs.set_default_args("bash", {"timeout": 30})
            
            # Mock tool_system that captures what it receives
            captured = {}
            class MockToolSystem:
                def call_tool(self, name, args):
                    captured["name"] = name
                    captured["args"] = args
                    return {"success": True, "output": "ok"}
            
            executor = ToolExecutor(
                tool_system=MockToolSystem(),
                tool_prefs=prefs,
            )
            
            # Execute a bash tool call without timeout
            import asyncio
            tool_call = {
                "id": "call_1",
                "function": {
                    "name": "bash",
                    "arguments": {"command": "echo hi"},
                },
            }
            
            # ToolExecutor.execute is async — use pytest.mark.asyncio
            import pytest
            # (Test decorated with @pytest.mark.asyncio)
            result = await executor.execute([tool_call])
            
            # Verify the mock received merged args
            assert captured["args"]["timeout"] == 30
            assert captured["args"]["command"] == "echo hi"
```

Run: `pytest tests/preferences/test_integration.py -v`
Expected: 1 passed

**Step 6: Commit**

```bash
git add vibe/core/coordinators.py vibe/core/query_loop_factory.py vibe/core/config.py vibe/cli/main.py tests/preferences/test_integration.py
git commit -m "feat(preferences): wire ToolPreferenceRegistry into ToolExecutor and CLI"
```

---

## Task A4: Gemini CLI Review for Phase A

**Prompt for Gemini CLI:**

```
Review the preference layer foundation (Phase A):

Files to review:
- vibe/preferences/__init__.py
- vibe/preferences/models.py
- vibe/preferences/registry.py
- vibe/preferences/tool_prefs.py
- vibe/core/coordinators.py (ToolExecutor changes)
- vibe/core/query_loop_factory.py (wiring)
- vibe/core/config.py (PreferenceConfig)
- vibe/cli/main.py (pref subcommands)
- tests/preferences/*.py

Focus on:
1. Is the registry pattern clean and extensible for 7 more preference types?
2. Are Pydantic models correct (v2 syntax, no v1 leftovers)?
3. Does ToolPreferenceRegistry.apply() correctly merge without overwriting user args?
4. Is the SQLite schema minimal but sufficient?
5. Are tests covering edge cases (glob matching, hit counting, removal)?
6. Is the CLI ergonomic?
7. Any import cycle risks?
8. Is the config backward-compatible (default-disabled)?

Report issues by severity: BLOCKER, WARNING, SUGGESTION.
```

Run in background: `gemini-cli review --files vibe/preferences/ vibe/core/coordinators.py vibe/core/query_loop_factory.py vibe/core/config.py vibe/cli/main.py tests/preferences/ > /tmp/phase_a_review.md`

Wait for review, fix issues, then proceed.

---

# ─────────────────────────────────────────
# PHASE B: P2 Approval Rules
# ─────────────────────────────────────────

## Overview

Mine user approval decisions to build auto-approve/deny rules. When user repeatedly approves "file read in ~/projects/", create a rule. When user repeatedly denies "rm -rf /", create a rule. Rules feed into `AutoApproveGate`/`AutoRejectGate`.

## Files

| File | Action |
|------|--------|
| `vibe/preferences/approval_rules.py` | **NEW** — `ApprovalPolicyDB` |
| `vibe/harness/security/approval_store.py` | Modify — add rule lookup |
| `vibe/harness/security/human_approval.py` | Modify — consult rules before LLM |
| `tests/preferences/test_approval_rules.py` | **NEW** |

## Task B1: Create ApprovalPolicyDB

**Objective:** Build rule database for approval decisions with pattern matching on tool + path + args.

**Step 1: Create `vibe/preferences/approval_rules.py`**

```python
"""Approval rule database — learned from user approval decisions."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class ApprovalPolicyDB:
    """Database of approval rules learned from user decisions.
    
    Rules are structured as: (tool_pattern, path_pattern, arg_constraints) → action
    """
    
    DOMAIN = "approval"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def add_rule(
        self,
        tool_pattern: str,
        action: "allow" | "deny" | "ask",
        path_pattern: str | None = None,
        arg_constraints: dict[str, Any] | None = None,
        min_confidence: float = 0.8,
    ) -> PreferenceRule:
        """Add an approval rule.
        
        Args:
            tool_pattern: Tool name or glob
            action: What to do when matched
            path_pattern: Optional path glob (for file tools)
            arg_constraints: Optional arg values that must match
            min_confidence: Required confidence for auto-execution (inferred rules)
        """
        rule = PreferenceRule(
            pattern=tool_pattern,
            action=action,
            action_args={
                "path_pattern": path_pattern,
                "arg_constraints": arg_constraints or {},
                "min_confidence": min_confidence,
            },
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            # Remove duplicate patterns
            self._policy.rules = [
                r for r in self._policy.rules
                if not (r.pattern == tool_pattern and r.action_args.get("path_pattern") == path_pattern)
            ]
            self._policy.add_rule(rule)
            self._save()
        return rule
    
    def check(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        tool_result_summary: str | None = None,
    ) -> "ApprovalDecision":
        """Check if an operation matches any approval rule.
        
        Returns ApprovalDecision with action and matched rule info.
        Deny rules are evaluated before allow rules for security.
        """
        if self._policy is None or not self._policy.enabled:
            return ApprovalDecision(action="ask", reason="no policy")
        
        # SECURITY: Evaluate deny rules first, then allow rules
        enabled_rules = self._policy.get_enabled_rules()
        deny_rules = [r for r in enabled_rules if r.action == "deny"]
        allow_rules = [r for r in enabled_rules if r.action == "allow"]
        
        for rule in deny_rules + allow_rules:
            match = self._matches(rule, tool_name, arguments)
            if match:
                # Batch hit count (not persisted immediately)
                self._registry.batch_hit(self.DOMAIN, rule.rule_id)
                return ApprovalDecision(
                    action=rule.action,  # type: ignore
                    reason=f"matched rule {rule.rule_id}: {rule.pattern}",
                    rule_id=rule.rule_id,
                )
        
        return ApprovalDecision(action="ask", reason="no matching rule")
    
    def _matches(self, rule: PreferenceRule, tool_name: str, arguments: dict[str, Any]) -> bool:
        """Check if a rule matches a tool invocation.
        
        SECURITY: All paths are resolved to absolute form before matching
        to prevent path traversal bypasses (e.g., /projects/../../../etc/shadow).
        """
        # Tool name match
        if not fnmatch.fnmatch(tool_name, rule.pattern):
            return False
        
        path_pattern = rule.action_args.get("path_pattern")
        if path_pattern:
            # Extract path from arguments (common for file/bash tools)
            raw_path = arguments.get("path") or arguments.get("file_path") or arguments.get("cwd") or ""
            # SECURITY: Resolve absolute path to prevent traversal bypass
            resolved = str(Path(raw_path).resolve())
            if not fnmatch.fnmatch(resolved, path_pattern):
                return False
        
        arg_constraints = rule.action_args.get("arg_constraints", {})
        for key, expected in arg_constraints.items():
            actual = arguments.get(key)
            if actual != expected:
                return False
        
        return True
    
    def learn_from_decision(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_decision: "allow" | "deny",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a user approval decision for future rule mining.
        
        This is called after every manual approval gate interaction.
        """
        # For MVP: immediately create a rule if pattern is clear
        # Future: batch mine with clustering
        path = arguments.get("path") or arguments.get("file_path") or arguments.get("cwd")
        if path and user_decision == "allow":
            # Create a broad rule: allow this tool in this directory
            dir_pattern = str(Path(path).parent) + "/*"
            self.add_rule(
                tool_pattern=tool_name,
                action="allow",
                path_pattern=dir_pattern,
            )
        elif user_decision == "deny":
            # Create a deny rule for exact args
            self.add_rule(
                tool_pattern=tool_name,
                action="deny",
                path_pattern=str(path) if path else None,
                arg_constraints={k: v for k, v in arguments.items() if k in ["command", "recursive"]},
            )


@dataclass
class ApprovalDecision:
    """Result of an approval policy check."""
    action: "allow" | "deny" | "ask"
    reason: str
    rule_id: str | None = None
```

**Step 2: Create tests**

Create `tests/preferences/test_approval_rules.py`:

```python
import tempfile
from pathlib import Path

from vibe.preferences.approval_rules import ApprovalPolicyDB
from vibe.preferences.registry import PreferenceRegistry


class TestApprovalPolicyDB:
    def test_allow_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))
            
            policy.add_rule("read_file", "allow", path_pattern="/home/user/projects/*")
            
            decision = policy.check("read_file", {"path": "/home/user/projects/code.py"})
            assert decision.action == "allow"
    
    def test_deny_rule_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))
            
            policy.add_rule("bash", "deny", arg_constraints={"command": "rm -rf /"})
            
            decision = policy.check("bash", {"command": "rm -rf /"})
            assert decision.action == "deny"
    
    def test_no_match_asks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))
            
            decision = policy.check("unknown_tool", {})
            assert decision.action == "ask"
    
    def test_learn_from_allow_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))
            
            policy.learn_from_decision("read_file", {"path": "/projects/a.py"}, "allow")
            
            # Should create a rule allowing read_file in /projects/*
            decision = policy.check("read_file", {"path": "/projects/b.py"})
            assert decision.action == "allow"
    
    def test_path_pattern_exact_vs_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            policy = ApprovalPolicyDB(PreferenceRegistry(str(db)))
            
            policy.add_rule("write_file", "allow", path_pattern="/tmp/*")
            
            assert policy.check("write_file", {"path": "/tmp/test.txt"}).action == "allow"
            assert policy.check("write_file", {"path": "/etc/passwd"}).action == "ask"
```

Run: `pytest tests/preferences/test_approval_rules.py -v`
Expected: 5 passed

**Step 3: Commit**

```bash
git add vibe/preferences/approval_rules.py tests/preferences/test_approval_rules.py
git commit -m "feat(preferences): add ApprovalPolicyDB with rule matching"
```

---

## Task B2: Wire ApprovalPolicyDB into approval gates

**Objective:** Consult learned rules before falling back to LLM-based smart approval.

**Step 1: Modify `vibe/harness/security/human_approval.py`**

Find `SmartApprovalGate` (or equivalent). Before calling LLM for approval, check `ApprovalPolicyDB`:

```python
from vibe.preferences.approval_rules import ApprovalPolicyDB

class SmartApprovalGate:
    def __init__(self, ...):
        ...
        self._policy_db = ApprovalPolicyDB()  # Lazy init
    
    async def check(self, tool_call: dict, context: dict) -> ApprovalResult:
        # NEW: Check learned rules first
        tool_name = extract_tool_name(tool_call)
        args = extract_arguments(tool_call)
        
        decision = self._policy_db.check(tool_name, args)
        if decision.action == "allow":
            return ApprovalResult(approved=True, reason=decision.reason)
        elif decision.action == "deny":
            return ApprovalResult(approved=False, reason=decision.reason)
        
        # Fall back to LLM-based approval
        ...
```

**Step 2: Log user decisions for learning**

After manual approval (in `CLIApprovalGate` or wherever user clicks yes/no), call:

```python
self._policy_db.learn_from_decision(tool_name, args, user_decision="allow")
```

**Step 3: Add CLI commands**

Under `pref_app`:

```python
@pref_app.command("approval-list")
def pref_approval_list():
    """List learned approval rules."""
    from vibe.preferences.approval_rules import ApprovalPolicyDB
    
    db = ApprovalPolicyDB()
    rules = db._policy.rules if db._policy else []
    ...

@pref_app.command("approval-clear")
def pref_approval_clear():
    """Clear all learned approval rules."""
    from vibe.preferences.approval_rules import ApprovalPolicyDB
    from vibe.preferences.registry import PreferenceRegistry
    
    PreferenceRegistry().delete_policy("approval")
    console.print("[green]✓[/green] Cleared all approval rules")
```

**Step 4: Commit**

```bash
git add vibe/harness/security/human_approval.py vibe/cli/main.py
git commit -m "feat(preferences): wire ApprovalPolicyDB into approval gates"
```

---

## Task B3: Gemini CLI Review for Phase B

**Prompt:**

```
Review Phase B (Approval Rules):

Files:
- vibe/preferences/approval_rules.py
- vibe/harness/security/human_approval.py
- tests/preferences/test_approval_rules.py

Focus:
1. Is the rule matching logic correct (glob + arg constraints)?
2. Does learn_from_decision create sensible rules or overfit?
3. Are deny rules properly prioritized over allow rules?
4. Is the integration with SmartApprovalGate clean?
5. Security: can inferred rules be exploited?
6. Tests cover allow, deny, no-match, learning?

Report by severity.
```

Run in background, fix, proceed.

---

# ─────────────────────────────────────────
# PHASE C: P3 Response Style Policy
# ─────────────────────────────────────────

## Overview

Capture user meta-feedback ("be concise", "show commands before running") and inject into system prompt. Mined from user corrections and explicit `vibe style set` commands.

## Files

| File | Action |
|------|--------|
| `vibe/preferences/style_policy.py` | **NEW** |
| `vibe/core/query_loop.py` | Modify — inject style into system prompt |
| `tests/preferences/test_style_policy.py` | **NEW** |

## Task C1: Implement ResponseStylePolicy

**Objective:** Structured style preferences with system prompt generation.

**Step 1: Create `vibe/preferences/style_policy.py`**

```python
"""Response style policy — user preferences for agent behavior and output format."""

from __future__ import annotations

from enum import Enum
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class Verbosity(str, Enum):
    TERSE = "terse"
    NORMAL = "normal"
    VERBOSE = "verbose"


class PlanFormat(str, Enum):
    BULLETS = "bullets"
    NUMBERED = "numbered"
    DAG = "dag"


class ConfirmThreshold(str, Enum):
    NEVER = "never"
    DESTRUCTIVE = "destructive"
    ALWAYS = "always"


class ResponseStylePolicy:
    """User preferences for agent response style.
    
    Mined from explicit commands and user corrections.
    """
    
    DOMAIN = "style"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def set_verbosity(self, level: Verbosity) -> None:
        self._set_field("verbosity", level.value)
    
    def set_plan_format(self, fmt: PlanFormat) -> None:
        self._set_field("plan_format", fmt.value)
    
    def set_confirm_threshold(self, threshold: ConfirmThreshold) -> None:
        self._set_field("confirm_threshold", threshold.value)
    
    def set_show_commands(self, show: bool) -> None:
        self._set_field("show_commands_before_run", show)
    
    def _set_field(self, key: str, value: Any) -> None:
        """Update a style field, replacing any existing rule."""
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
        self._policy.add_rule(PreferenceRule(
            pattern=key,
            action="set",
            action_args={"value": value},
            source=PreferenceSource.EXPLICIT,
        ))
        self._save()
    
    def get_system_prompt_append(self) -> str:
        """Generate system prompt additions from style preferences."""
        if self._policy is None or not self._policy.enabled:
            return ""
        
        parts = []
        for rule in self._policy.get_enabled_rules():
            val = rule.action_args.get("value")
            if rule.pattern == "verbosity":
                if val == "terse":
                    parts.append("Be concise. Use minimal words. Avoid pleasantries.")
                elif val == "verbose":
                    parts.append("Be thorough. Explain reasoning step by step.")
            elif rule.pattern == "plan_format":
                parts.append(f"Format multi-step plans as {val}.")
            elif rule.pattern == "confirm_threshold":
                if val == "never":
                    parts.append("Never ask for confirmation. Just execute.")
                elif val == "destructive":
                    parts.append("Only ask for confirmation on destructive operations (delete, overwrite).")
            elif rule.pattern == "show_commands_before_run":
                if val:
                    parts.append("Always show the exact command before executing it.")
        
        return "\n".join(parts)
    
    def get_field(self, key: str, default: Any = None) -> Any:
        """Get a style field value."""
        if self._policy is None:
            return default
        for rule in self._policy.rules:
            if rule.pattern == key:
                return rule.action_args.get("value", default)
        return default
```

**Step 2: Create tests**

Create `tests/preferences/test_style_policy.py`:

```python
import tempfile
from pathlib import Path

from vibe.preferences.style_policy import ResponseStylePolicy, Verbosity, PlanFormat, ConfirmThreshold
from vibe.preferences.registry import PreferenceRegistry


class TestResponseStylePolicy:
    def test_set_and_get_verbosity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))
            
            style.set_verbosity(Verbosity.TERSE)
            assert style.get_field("verbosity") == "terse"
            prompt = style.get_system_prompt_append()
            assert "concise" in prompt
    
    def test_system_prompt_combines_multiple(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))
            
            style.set_verbosity(Verbosity.TERSE)
            style.set_confirm_threshold(ConfirmThreshold.NEVER)
            style.set_show_commands(True)
            
            prompt = style.get_system_prompt_append()
            assert "concise" in prompt
            assert "Never ask" in prompt
            assert "show the exact command" in prompt
    
    def test_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))
            
            style.set_verbosity(Verbosity.TERSE)
            style.set_verbosity(Verbosity.VERBOSE)
            
            assert style.get_field("verbosity") == "verbose"
            prompt = style.get_system_prompt_append()
            assert "thorough" in prompt
            assert "concise" not in prompt
    
    def test_empty_policy_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "prefs.db"
            style = ResponseStylePolicy(PreferenceRegistry(str(db)))
            
            assert style.get_system_prompt_append() == ""
```

Run: `pytest tests/preferences/test_style_policy.py -v`
Expected: 4 passed

**Step 3: Wire into QueryLoop**

In `vibe/core/query_loop.py`, when building the system prompt, append style:

```python
from vibe.preferences.style_policy import ResponseStylePolicy

# In QueryLoop.__init__ or where system prompt is built:
style_policy = ResponseStylePolicy()
style_append = style_policy.get_system_prompt_append()
if style_append:
    system_prompt += "\n\n" + style_append
```

**Step 4: Add CLI**

```python
@pref_app.command("style-set")
def pref_style_set(
    key: str = typer.Argument(..., help="Style key: verbosity|plan_format|confirm_threshold|show_commands"),
    value: str = typer.Argument(..., help="Value for the key"),
):
    """Set a response style preference."""
    from vibe.preferences.style_policy import ResponseStylePolicy
    
    style = ResponseStylePolicy()
    if key == "verbosity":
        style.set_verbosity(value)
    elif key == "plan_format":
        style.set_plan_format(value)
    elif key == "confirm_threshold":
        style.set_confirm_threshold(value)
    elif key == "show_commands":
        style.set_show_commands(value.lower() == "true")
    else:
        console.print(f"[red]Unknown style key: {key}[/red]")
        raise typer.Exit(1)
    
    console.print(f"[green]✓[/green] Set {key} = {value}")

@pref_app.command("style-show")
def pref_style_show():
    """Show current style preferences."""
    from vibe.preferences.style_policy import ResponseStylePolicy
    
    style = ResponseStylePolicy()
    prompt = style.get_system_prompt_append()
    if prompt:
        console.print("[bold]Active style injections:[/bold]")
        console.print(prompt)
    else:
        console.print("[dim]No style preferences set.[/dim]")
```

**Step 5: Commit**

```bash
git add vibe/preferences/style_policy.py tests/preferences/test_style_policy.py vibe/core/query_loop.py vibe/cli/main.py
git commit -m "feat(preferences): add ResponseStylePolicy with system prompt injection"
```

---

## Task C2: Gemini CLI Review for Phase C

**Prompt:**

```
Review Phase C (Response Style Policy):

Files:
- vibe/preferences/style_policy.py
- vibe/core/query_loop.py (style injection)
- tests/preferences/test_style_policy.py

Focus:
1. Is the system prompt injection clean (no prompt injection vulnerabilities)?
2. Do style preferences correctly override without breaking existing prompts?
3. Is the CLI interface intuitive?
4. Are all 4 style dimensions tested?
5. Does the prompt append handle empty policy correctly?
```

Run, fix, proceed.

---

# ─────────────────────────────────────────
# PHASE D: P4 Macro Sessions
# ─────────────────────────────────────────

## Overview

User-defined multi-step workflows as YAML DAGs with Jinja2 templating. Triggered via cron or `vibe macro run`. Reuses `DAGPlanner` structure.

## Files

| File | Action |
|------|--------|
| `vibe/preferences/macro_session.py` | **NEW** |
| `vibe/cli/main.py` | Modify — add `vibe macro` subcommands |
| `tests/preferences/test_macro_session.py` | **NEW** |

## Task D1: Implement MacroSession runner

**Objective:** YAML-defined workflows that execute a sequence of vibe queries with result passing.

**Step 1: Create `vibe/preferences/macro_session.py`**

```python
"""Macro session runner — user-defined multi-step workflows."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vibe.harness.dag_planner import DAGPlanner, DAGNode, DAGPlanResult


@dataclass
class MacroStep:
    """A single step in a macro workflow."""
    name: str
    query: str                    # Jinja2 template
    store_result_as: str | None = None  # variable name for downstream use
    condition: str | None = None  # Jinja2 condition (skip if false)
    timeout: int = 300


@dataclass
class MacroSession:
    """A user-defined workflow session."""
    name: str
    description: str = ""
    trigger: str | None = None    # "cron: 0 9 * * *" or "manual"
    steps: list[MacroStep] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


class MacroSessionRunner:
    """Execute macro sessions by converting to DAG and running via QueryLoop."""
    
    MACRO_DIR = Path.home() / ".vibe" / "macros"
    
    def __init__(self, query_loop_factory: Any | None = None) -> None:
        self.factory = query_loop_factory
        self._planner = DAGPlanner()
    
    def list_macros(self) -> list[MacroSession]:
        """List all saved macro sessions."""
        macros = []
        if self.MACRO_DIR.exists():
            for f in self.MACRO_DIR.glob("*.yaml"):
                with open(f) as fh:
                    data = yaml.safe_load(fh)
                    macros.append(self._dict_to_macro(data))
        return macros
    
    def load_macro(self, name: str) -> MacroSession | None:
        """Load a macro by name."""
        path = self.MACRO_DIR / f"{name}.yaml"
        if not path.exists():
            return None
        with open(path) as f:
            data = yaml.safe_load(f)
        return self._dict_to_macro(data)
    
    def save_macro(self, macro: MacroSession) -> None:
        """Save a macro to disk."""
        self.MACRO_DIR.mkdir(parents=True, exist_ok=True)
        path = self.MACRO_DIR / f"{macro.name}.yaml"
        with open(path, "w") as f:
            yaml.dump(self._macro_to_dict(macro), f, default_flow_style=False)
    
    def run(self, macro: MacroSession, initial_vars: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a macro session.
        
        Returns dict of all stored variables.
        SECURITY: Uses SandboxedEnvironment to prevent SSTI/RCE from untrusted inputs.
        """
        from jinja2.sandbox import SandboxedEnvironment
        
        env = SandboxedEnvironment()
        variables = dict(initial_vars or {})
        variables.update(macro.variables)
        
        results = {}
        
        for i, step in enumerate(macro.steps):
            # Evaluate condition
            if step.condition:
                cond_template = env.from_string(step.condition)
                cond_result = cond_template.render(**variables)
                if cond_result.strip().lower() in ("false", "0", "", "none"):
                    continue
            
            # Render query with variables
            query_template = env.from_string(step.query)
            query = query_template.render(**variables)
            
            # Execute via QueryLoop (simplified — real impl would use factory)
            result = self._execute_query(query, step.timeout)
            
            if step.store_result_as:
                variables[step.store_result_as] = result
                results[step.store_result_as] = result
        
        return results
    
    def _execute_query(self, query: str, timeout: int) -> str:
        """Execute a single query. Stub — real impl uses QueryLoop."""
        # Placeholder: real implementation would create a QueryLoop and run
        return f"[Result for: {query[:50]}...]"
    
    def _dict_to_macro(self, data: dict) -> MacroSession:
        steps = [MacroStep(**s) for s in data.get("steps", [])]
        return MacroSession(
            name=data["name"],
            description=data.get("description", ""),
            trigger=data.get("trigger"),
            steps=steps,
            variables=data.get("variables", {}),
        )
    
    def _macro_to_dict(self, macro: MacroSession) -> dict:
        return {
            "name": macro.name,
            "description": macro.description,
            "trigger": macro.trigger,
            "steps": [
                {"name": s.name, "query": s.query, "store_result_as": s.store_result_as,
                 "condition": s.condition, "timeout": s.timeout}
                for s in macro.steps
            ],
            "variables": macro.variables,
        }
```

**Step 2: Create tests**

Create `tests/preferences/test_macro_session.py`:

```python
import tempfile
from pathlib import Path

from vibe.preferences.macro_session import MacroSession, MacroStep, MacroSessionRunner


class TestMacroSession:
    def test_run_simple_sequence(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="test",
            steps=[
                MacroStep(name="step1", query="Hello {{name}}", store_result_as="greeting"),
                MacroStep(name="step2", query="Say {{greeting}} again"),
            ],
        )
        
        results = runner.run(macro, {"name": "World"})
        assert "greeting" in results
    
    def test_condition_skip(self):
        runner = MacroSessionRunner()
        macro = MacroSession(
            name="conditional",
            steps=[
                MacroStep(name="always", query="run", store_result_as="ran"),
                MacroStep(name="skip", query="skip me", condition="{{skip}}", store_result_as="skipped"),
            ],
        )
        
        results = runner.run(macro, {"skip": False})
        assert "ran" in results
        assert "skipped" not in results
    
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Temporarily override MACRO_DIR
            original = MacroSessionRunner.MACRO_DIR
            MacroSessionRunner.MACRO_DIR = Path(tmp)
            
            try:
                runner = MacroSessionRunner()
                macro = MacroSession(name="saved", steps=[MacroStep(name="s", query="test")])
                runner.save_macro(macro)
                
                loaded = runner.load_macro("saved")
                assert loaded is not None
                assert loaded.name == "saved"
                assert len(loaded.steps) == 1
            finally:
                MacroSessionRunner.MACRO_DIR = original
```

Run: `pytest tests/preferences/test_macro_session.py -v`
Expected: 3 passed

**Step 3: Add CLI**

```python
macro_app = typer.Typer(help="Macro session workflows")
app.add_typer(macro_app, name="macro")

@macro_app.command("list")
def macro_list():
    """List saved macro sessions."""
    from vibe.preferences.macro_session import MacroSessionRunner
    
    runner = MacroSessionRunner()
    macros = runner.list_macros()
    if not macros:
        console.print("[dim]No macros saved.[/dim]")
        return
    
    table = Table(title="Macro Sessions")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="dim")
    table.add_column("Trigger", style="yellow")
    table.add_column("Steps", style="green")
    
    for m in macros:
        table.add_row(m.name, m.description, m.trigger or "manual", str(len(m.steps)))
    console.print(table)

@macro_app.command("run")
def macro_run(
    name: str = typer.Argument(..., help="Macro name to run"),
    var: list[str] = typer.Option([], "--var", help="Variables as key=val"),
):
    """Run a macro session."""
    from vibe.preferences.macro_session import MacroSessionRunner
    import shlex
    
    runner = MacroSessionRunner()
    macro = runner.load_macro(name)
    if macro is None:
        console.print(f"[red]Macro '{name}' not found.[/red]")
        raise typer.Exit(1)
    
    variables = {}
    for v in var:
        if "=" in v:
            k, val = v.split("=", 1)
            variables[k] = val
    
    console.print(f"[green]Running macro:[/green] {name}")
    results = runner.run(macro, variables)
    
    console.print("\n[bold]Results:[/bold]")
    for k, v in results.items():
        console.print(f"  {k}: {v}")

@macro_app.command("create")
def macro_create(
    name: str = typer.Argument(..., help="Macro name"),
    description: str = typer.Option("", "--desc", "-d"),
):
    """Create a new macro session interactively."""
    from vibe.preferences.macro_session import MacroSession, MacroStep, MacroSessionRunner
    
    console.print("[dim]Enter steps (empty query to finish):[/dim]")
    steps = []
    while True:
        step_name = console.input("Step name: ")
        query = console.input("Query template: ")
        if not query:
            break
        store_as = console.input("Store result as (optional): ") or None
        steps.append(MacroStep(name=step_name, query=query, store_result_as=store_as))
    
    macro = MacroSession(name=name, description=description, steps=steps)
    runner = MacroSessionRunner()
    runner.save_macro(macro)
    console.print(f"[green]✓[/green] Saved macro '{name}' with {len(steps)} steps")
```

**Step 4: Commit**

```bash
git add vibe/preferences/macro_session.py tests/preferences/test_macro_session.py vibe/cli/main.py
git commit -m "feat(preferences): add MacroSession runner with YAML persistence"
```

---

## Task D2: Gemini CLI Review for Phase D

**Prompt:**

```
Review Phase D (Macro Sessions):

Files:
- vibe/preferences/macro_session.py
- tests/preferences/test_macro_session.py
- vibe/cli/main.py (macro commands)

Focus:
1. Is the Jinja2 templating safe (no code execution)?
2. Does the macro runner correctly pass variables between steps?
3. Is the YAML format intuitive?
4. Are conditions evaluated correctly (skip on false)?
5. Is the CLI create flow usable?
6. What's the integration plan with real QueryLoop (currently stubbed)?
```

Run, fix, proceed.

---

# ─────────────────────────────────────────
# PHASE E: P5 Recovery Rules + P6 Compaction Policy
# ─────────────────────────────────────────

## Overview

Two independent workstreams:
- **P5**: Domain-specific error recovery rules ("on permission denied, try sudo")
- **P6**: User-tuned compaction strategy preferences

## Files

| File | Action |
|------|--------|
| `vibe/preferences/recovery_rules.py` | **NEW** |
| `vibe/preferences/compaction_policy.py` | **NEW** |
| `vibe/core/error_recovery.py` | Modify — hook recovery rules |
| `vibe/core/coordinators.py` | Modify — hook compaction policy |
| `tests/preferences/test_recovery_rules.py` | **NEW** |
| `tests/preferences/test_compaction_policy.py` | **NEW** |

## Task E1: Implement RecoveryRuleDB

**Objective:** Pattern-based recovery actions for known error types.

**Step 1: Create `vibe/preferences/recovery_rules.py`**

```python
"""Recovery rule database — learned error recovery strategies."""

from __future__ import annotations

import re
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class RecoveryRuleDB:
    """Database of recovery rules for known failure patterns.
    
    Rules: (error_regex, tool_name) → recovery_action
    """
    
    DOMAIN = "recovery"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def add_rule(
        self,
        error_pattern: str,
        recovery_action: str,
        recovery_args: dict[str, Any],
        tool_name: str | None = None,
        max_attempts: int = 1,
    ) -> PreferenceRule:
        """Add a recovery rule.
        
        Args:
            error_pattern: Regex to match against stderr/exception message
            recovery_action: "retry_with", "fallback_to", "ask_user"
            recovery_args: Action-specific args
            tool_name: Optional tool filter
            max_attempts: Max times to apply this rule per session
        """
        rule = PreferenceRule(
            pattern=error_pattern,
            action=recovery_action,
            action_args={
                "recovery_args": recovery_args,
                "tool_name": tool_name,
                "max_attempts": max_attempts,
                "attempt_count": 0,
            },
            source=PreferenceSource.INFERRED,
        )
        if self._policy:
            self._policy.add_rule(rule)
            self._save()
        return rule
    
    def find_recovery(
        self,
        error_message: str,
        tool_name: str | None = None,
        session_state: dict[str, Any] | None = None,
    ) -> PreferenceRule | None:
        """Find a matching recovery rule for an error.
        
        NOTE: attempt_count is tracked in session_state (in-memory), NOT in the DB.
        This prevents permanent lockout if a session crashes before reset.
        """
        if self._policy is None or not self._policy.enabled:
            return None
        
        # Use caller-provided session state for attempt tracking
        state = session_state or {}
        recovery_key = f"recovery_attempts_{tool_name or 'global'}"
        if recovery_key not in state:
            state[recovery_key] = {}
        
        for rule in self._policy.get_enabled_rules():
            # Check error pattern match
            if not re.search(rule.pattern, error_message, re.IGNORECASE):
                continue
            
            # Check tool filter
            rule_tool = rule.action_args.get("tool_name")
            if rule_tool and rule_tool != tool_name:
                continue
            
            # Check attempt limit (from session state, not DB)
            attempts = state[recovery_key].get(rule.rule_id, 0)
            max_attempts = rule.action_args.get("max_attempts", 1)
            if attempts >= max_attempts:
                continue
            
            # Increment in-memory attempt count
            state[recovery_key][rule.rule_id] = attempts + 1
            # Batch hit count for persistence (flushed on session end)
            self._registry.batch_hit(self.DOMAIN, rule.rule_id)
            
            return rule
        
        return None
```

**Step 2: Create `vibe/preferences/compaction_policy.py`**

```python
"""Compaction policy — user preferences for context truncation strategy."""

from __future__ import annotations

from enum import Enum
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class CompactionStrategy(str, Enum):
    TRUNCATE = "truncate"
    LLM_SUMMARIZE = "llm_summarize"
    OFFLOAD = "offload"
    DROP = "drop"


class CompactionPolicy:
    """User preferences for context compaction behavior."""
    
    DOMAIN = "compaction"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def set_strategy(self, strategy: CompactionStrategy) -> None:
        self._set_field("preferred_strategy", strategy.value)
    
    def set_drop_priority(self, roles: list[str]) -> None:
        """Set priority order for dropping messages (first = drop first)."""
        self._set_field("drop_priority", roles)
    
    def set_never_summarize(self, sources: list[str]) -> None:
        """Sources that should never be LLM-summarized."""
        self._set_field("never_summarize", sources)
    
    def set_offload_threshold(self, tokens: int) -> None:
        self._set_field("offload_threshold", tokens)
    
    def _set_field(self, key: str, value: Any) -> None:
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
        self._policy.add_rule(PreferenceRule(
            pattern=key,
            action="set",
            action_args={"value": value},
            source=PreferenceSource.EXPLICIT,
        ))
        self._save()
    
    def get_strategy(self) -> CompactionStrategy:
        val = self._get_field("preferred_strategy", CompactionStrategy.TRUNCATE)
        return CompactionStrategy(val)
    
    def get_drop_priority(self) -> list[str]:
        return self._get_field("drop_priority", ["assistant", "tool_result", "user"])
    
    def get_never_summarize(self) -> list[str]:
        return self._get_field("never_summarize", ["system"])
    
    def get_offload_threshold(self) -> int:
        return self._get_field("offload_threshold", 4000)
    
    def _get_field(self, key: str, default: Any) -> Any:
        if self._policy is None:
            return default
        for rule in self._policy.rules:
            if rule.pattern == key:
                return rule.action_args.get("value", default)
        return default
```

**Step 3: Wire into existing coordinators**

In `vibe/core/error_recovery.py`, before generic retry, check `RecoveryRuleDB`:

```python
from vibe.preferences.recovery_rules import RecoveryRuleDB

# In ErrorRecovery.recover():
recovery_db = RecoveryRuleDB()
rule = recovery_db.find_recovery(error_message, tool_name)
if rule:
    if rule.action == "retry_with":
        # Apply recovery args and retry
        ...
    elif rule.action == "fallback_to":
        # Switch to fallback tool
        ...
```

In `vibe/core/coordinators.py`, in `CompactionCoordinator`, consult `CompactionPolicy`:

```python
from vibe.preferences.compaction_policy import CompactionPolicy

# In CompactionCoordinator.compact():
policy = CompactionPolicy()
strategy = policy.get_strategy()
# Override default strategy with user preference
```

**Step 4: Create tests**

Create `tests/preferences/test_recovery_rules.py` and `tests/preferences/test_compaction_policy.py` (similar patterns to prior tests — test set/get, rule matching, attempt limits).

**Step 5: Commit**

```bash
git add vibe/preferences/recovery_rules.py vibe/preferences/compaction_policy.py vibe/core/error_recovery.py vibe/core/coordinators.py tests/preferences/test_recovery_rules.py tests/preferences/test_compaction_policy.py
git commit -m "feat(preferences): add RecoveryRuleDB and CompactionPolicy"
```

---

## Task E2: Gemini CLI Review for Phase E

**Prompt:**

```
Review Phase E (Recovery Rules + Compaction Policy):

Files:
- vibe/preferences/recovery_rules.py
- vibe/preferences/compaction_policy.py
- vibe/core/error_recovery.py
- vibe/core/coordinators.py
- tests/preferences/test_recovery_rules.py
- tests/preferences/test_compaction_policy.py

Focus:
1. Does RecoveryRuleDB correctly match regex patterns against error messages?
2. Are attempt limits enforced (prevent infinite loops)?
3. Does CompactionPolicy correctly override default strategies?
4. Are the integrations non-breaking (default-disabled)?
5. Test coverage for attempt limits, strategy override, drop priority?
```

Run, fix, proceed.

---

# ─────────────────────────────────────────
# PHASE F: P7 Provider Preference + P8 Extraction Policy
# ─────────────────────────────────────────

## Overview

Two final independent workstreams:
- **P7**: Learned provider/model preferences per task type
- **P8**: User-tuned wiki knowledge extraction rules

## Files

| File | Action |
|------|--------|
| `vibe/preferences/provider_prefs.py` | **NEW** |
| `vibe/preferences/extraction_policy.py` | **NEW** |
| `vibe/core/cost_router.py` | Modify — consult provider prefs |
| `vibe/memory/extraction.py` | Modify — consult extraction policy |
| `tests/preferences/test_provider_prefs.py` | **NEW** |
| `tests/preferences/test_extraction_policy.py` | **NEW** |

## Task F1: Implement ProviderPreferenceMatrix

**Objective:** Learn which model/provider user prefers for which task type.

**Step 1: Create `vibe/preferences/provider_prefs.py`**

```python
"""Provider preference matrix — learned model routing preferences."""

from __future__ import annotations

from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class ProviderPreferenceMatrix:
    """User preferences for model/provider selection per task type.
    
    Mined from user model override history.
    """
    
    DOMAIN = "provider"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def record_choice(
        self,
        task_type: str,
        chosen_model: str,
        available_models: list[str],
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record a model choice for a task type.
        
        Called whenever user overrides model or CostRouter makes a selection.
        """
        if self._policy is None:
            return
        
        # Update or create rule for this task type
        self._policy.rules = [r for r in self._policy.rules if r.pattern != task_type]
        self._policy.add_rule(PreferenceRule(
            pattern=task_type,
            action="prefer_model",
            action_args={
                "model": chosen_model,
                "fallbacks": [m for m in available_models if m != chosen_model],
                "context": context or {},
                "choice_count": 1,
            },
            source=PreferenceSource.INFERRED,
        ))
        self._save()
    
    def get_preferred_model(
        self,
        task_type: str,
        default_model: str,
        min_confidence: int = 2,
    ) -> str:
        """Get preferred model for a task type.
        
        Args:
            task_type: e.g., "coding", "review", "creative", "analysis"
            default_model: Fallback if no preference or confidence too low
            min_confidence: Minimum choice_count to trust the preference
        """
        if self._policy is None or not self._policy.enabled:
            return default_model
        
        for rule in self._policy.get_enabled_rules():
            if rule.pattern == task_type:
                count = rule.action_args.get("choice_count", 0)
                if count >= min_confidence:
                    return rule.action_args.get("model", default_model)
        
        return default_model
    
    def get_fallback_chain(self, task_type: str) -> list[str]:
        """Get preferred fallback chain for a task type."""
        if self._policy is None:
            return []
        
        for rule in self._policy.get_enabled_rules():
            if rule.pattern == task_type:
                return rule.action_args.get("fallbacks", [])
        
        return []
```

**Step 2: Create `vibe/preferences/extraction_policy.py`**

```python
"""Extraction policy — user preferences for wiki knowledge extraction."""

from __future__ import annotations

from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule, PreferenceSource
from vibe.preferences.registry import PreferenceRegistry


class ExtractionPolicy:
    """User preferences for KnowledgeExtractor behavior.
    
    Controls what gets extracted, how it's tagged, and merge behavior.
    """
    
    DOMAIN = "extraction"
    
    def __init__(self, registry: PreferenceRegistry | None = None) -> None:
        self._registry = registry or PreferenceRegistry()
        self._policy: PreferencePolicy | None = None
        self._load()
    
    def _load(self) -> None:
        self._policy = self._registry.load_policy(self.DOMAIN)
        if self._policy is None:
            self._policy = PreferencePolicy(domain=self.DOMAIN)
    
    def _save(self) -> None:
        if self._policy:
            self._registry.save_policy(self._policy)
    
    def add_skip_pattern(self, pattern: str) -> None:
        """Skip queries matching this pattern (e.g., "finance", "password")."""
        if self._policy is None:
            return
        # Store as a rule with action "skip"
        existing = [r for r in self._policy.rules if r.pattern == pattern and r.action == "skip"]
        if not existing:
            self._policy.add_rule(PreferenceRule(
                pattern=pattern,
                action="skip",
                source=PreferenceSource.EXPLICIT,
            ))
            self._save()
    
    def add_auto_tag(self, keyword: str, tag: str) -> None:
        """Auto-tag extracted pages containing keyword with tag."""
        if self._policy is None:
            return
        self._policy.add_rule(PreferenceRule(
            pattern=keyword,
            action="auto_tag",
            action_args={"tag": tag},
            source=PreferenceSource.EXPLICIT,
        ))
        self._save()
    
    def should_skip(self, query: str) -> bool:
        """Check if a query should be skipped based on patterns."""
        if self._policy is None or not self._policy.enabled:
            return False
        
        for rule in self._policy.get_enabled_rules():
            if rule.action == "skip" and rule.pattern.lower() in query.lower():
                return True
        
        return False
    
    def get_tags_for_content(self, content: str) -> list[str]:
        """Get auto-tags for content based on keyword matches."""
        if self._policy is None:
            return []
        
        tags = []
        for rule in self._policy.get_enabled_rules():
            if rule.action == "auto_tag" and rule.pattern.lower() in content.lower():
                tag = rule.action_args.get("tag")
                if tag:
                    tags.append(tag)
        
        return tags
    
    def set_merge_threshold(self, threshold: float) -> None:
        """Set similarity threshold for merging duplicate pages."""
        self._set_field("merge_threshold", threshold)
    
    def get_merge_threshold(self) -> float:
        return self._get_field("merge_threshold", 0.85)
    
    def _set_field(self, key: str, value: Any) -> None:
        if self._policy is None:
            return
        self._policy.rules = [r for r in self._policy.rules if r.pattern != key]
        self._policy.add_rule(PreferenceRule(
            pattern=key,
            action="set",
            action_args={"value": value},
            source=PreferenceSource.EXPLICIT,
        ))
        self._save()
    
    def _get_field(self, key: str, default: Any) -> Any:
        if self._policy is None:
            return default
        for rule in self._policy.rules:
            if rule.pattern == key:
                return rule.action_args.get("value", default)
        return default
```

**Step 3: Wire into CostRouter and KnowledgeExtractor**

In `vibe/core/cost_router.py`, in `CostRouter.route()`, consult `ProviderPreferenceMatrix`:

```python
from vibe.preferences.provider_prefs import ProviderPreferenceMatrix

# In route():
pref_matrix = ProviderPreferenceMatrix()
preferred = pref_matrix.get_preferred_model(task_type, default_model=chosen.model_id)
if preferred != chosen.model_id:
    # Override with user preference if confidence high enough
    ...
```

In `vibe/memory/extraction.py`, in `KnowledgeExtractor.extract()`, consult `ExtractionPolicy`:

```python
from vibe.preferences.extraction_policy import ExtractionPolicy

# In extract():
policy = ExtractionPolicy()
if policy.should_skip(query):
    logger.info(f"Skipping extraction for query (matches skip pattern): {query[:50]}")
    return None

# After extraction, apply auto-tags:
tags = policy.get_tags_for_content(content)
```

**Step 4: Create tests and commit**

Create `tests/preferences/test_provider_prefs.py` and `tests/preferences/test_extraction_policy.py`.

```bash
git add vibe/preferences/provider_prefs.py vibe/preferences/extraction_policy.py vibe/core/cost_router.py vibe/memory/extraction.py tests/preferences/test_provider_prefs.py tests/preferences/test_extraction_policy.py
git commit -m "feat(preferences): add ProviderPreferenceMatrix and ExtractionPolicy"
```

---

## Task F2: Gemini CLI Review for Phase F

**Prompt:**

```
Review Phase F (Provider Preferences + Extraction Policy):

Files:
- vibe/preferences/provider_prefs.py
- vibe/preferences/extraction_policy.py
- vibe/core/cost_router.py
- vibe/memory/extraction.py
- tests/preferences/test_provider_prefs.py
- tests/preferences/test_extraction_policy.py

Focus:
1. Does ProviderPreferenceMatrix correctly track choice_count for confidence?
2. Is the fallback chain properly stored and retrieved?
3. Does ExtractionPolicy skip work (case-insensitive substring)?
4. Are auto-tags correctly applied to content?
5. Are the integrations non-breaking?
6. Is the merge threshold configurable and properly defaulted?
```

Run, fix, proceed.

---

# ─────────────────────────────────────────
# PHASE G: Integration & Regression
# ─────────────────────────────────────────

## Task G1: Full test suite regression

```bash
cd /Users/rsong/DevSpace/vibe-agent
pytest tests/ --ignore=tests/test_dashboard_api.py --ignore=tests/test_config.py --ignore=tests/test_config_providers.py --ignore=tests/core/test_config_security.py -q
```

Expected: 983+ passing (baseline) + all new tests passing

## Task G2: Lint and format

```bash
ruff check vibe/preferences/ tests/preferences/
ruff format vibe/preferences/ tests/preferences/
```

Expected: Clean

## Task G3: Verify CLI commands

```bash
python -m vibe pref tool-list
python -m vibe pref style-show
python -m vibe macro list
```

Expected: No errors, empty lists shown gracefully

## Task G4: Bulk Gemini CLI Review

**Prompt:**

```
Bulk review of the entire Preference Layer (Phases A-F):

New modules:
- vibe/preferences/ (8 files)
- tests/preferences/ (8+ test files)

Modified modules:
- vibe/core/coordinators.py
- vibe/core/query_loop.py
- vibe/core/query_loop_factory.py
- vibe/core/config.py
- vibe/core/error_recovery.py
- vibe/core/cost_router.py
- vibe/memory/extraction.py
- vibe/harness/security/human_approval.py
- vibe/cli/main.py

Review criteria:
1. Architecture: Is the registry pattern consistently applied across all 8 preference types?
2. Backward compatibility: Are all features default-disabled? Does existing behavior unchanged when disabled?
3. Test coverage: Are all 8 types tested? Are integration points tested?
4. Security: Can inferred rules be exploited? Is Jinja2 templating sandboxed?
5. Performance: Is SQLite WAL mode used? Are there N+1 queries?
6. CLI UX: Are commands discoverable and consistent?
7. Code quality: DRY, YAGNI, no duplication between preference types?
8. Integration: Do hooks correctly fire without breaking existing coordinators?

Categorize all findings by severity and workstream.
```

Run in background, fix all blockers and warnings.

## Task G5: Final commit and documentation

```bash
git add -A
git commit -m "feat(preferences): complete Preference Layer (8 types)

- Tool Preferences: default arg overrides
- Approval Rules: learned auto-approve/deny
- Response Style: system prompt injection
- Macro Sessions: YAML workflow runner
- Recovery Rules: error pattern recovery
- Compaction Policy: user-tuned truncation strategy
- Provider Preference: per-task model routing
- Extraction Policy: wiki knowledge filtering

All features default-disabled. Registry pattern with SQLite WAL.
983+ tests passing."
```

Update `docs/ROADMAP.md` to mark preference layer complete.

---

# Verification Checklist

| Check | Command | Expected |
|-------|---------|----------|
| Full suite | `pytest tests/ --ignore=tests/test_dashboard_api.py --ignore=tests/test_config.py --ignore=tests/test_config_providers.py --ignore=tests/core/test_config_security.py -q` | 1000+ passing |
| Preference tests | `pytest tests/preferences/ -v` | All pass |
| Lint | `ruff check vibe/preferences/ tests/preferences/` | Clean |
| Format | `ruff format --check vibe/preferences/ tests/preferences/` | Clean |
| Import check | `python -c "from vibe.preferences import *; print('OK')"` | OK |
| CLI help | `python -m vibe pref --help` | Shows 4+ commands |
| Macro help | `python -m vibe macro --help` | Shows 3 commands |
| Prune stale | `python -m vibe pref prune --dry-run` | Shows rules to prune |

---

# Rollback Plan

Each preference type can be individually disabled:

```yaml
# ~/.vibe/config.yaml
preferences:
  enabled: true        # Master switch (set false to disable entire layer)
  tools_enabled: false
  approval_enabled: false
  style_enabled: false
  macros_enabled: false
  recovery_enabled: false
  compaction_enabled: false
  provider_enabled: false
  extraction_enabled: false
```

To completely remove: Delete `~/.vibe/memory/preferences.db` and set `preferences.enabled: false`.

### Post-Implementation Review Fixes (from Gemini CLI)

The following issues were identified during review and patched into the plan:

1. **Path traversal in approval rules** — All paths resolved with `Path.resolve()` before `fnmatch`
2. **Session state in global DB** — `attempt_count` moved to in-memory session state; `hit_count` batched and flushed on shutdown
3. **JSON blob race conditions** — `batch_hit()` + `flush_hits()` pattern replaces per-hit DB writes
4. **Jinja2 RCE in macros** — `SandboxedEnvironment` replaces standard `Template`
5. **Allow/deny priority** — Deny rules evaluated before allow rules
6. **Pydantic migration fragility** — `extra="ignore"` added to all models
7. **Stale rule pruning** — `prune_stale()` method added to registry
8. **Phase order** — Phase C (Style) moved before Phase B (Approval) for momentum
9. **Async tests** — `pytest.mark.asyncio` replaces `asyncio.run()`

---

# Appendix: Design Rationale

## Why SQLite over YAML/JSON files?

- Atomic updates (INSERT OR REPLACE)
- Concurrent access (WAL mode)
- Queryable (list, filter, count)
- Same infrastructure as SessionStore, TraceStore, SpendTracker

## Why PreferenceRule as universal atom?

- All 8 types share: pattern, action, args, confidence, source, hit_count
- Registry operations are generic (save/load/list/delete)
- New preference types need only domain-specific logic, not new storage

## Why default-disabled?

- Backward compatibility: existing users see no behavior change
- Opt-in builds trust: user explicitly enables, then benefits accumulate
- Safety: inferred rules can't accidentally activate without user awareness

## Why not use Pydantic for the policy JSON?

- `PreferencePolicy` IS Pydantic (stored as JSON in SQLite)
- `PreferenceRule` IS Pydantic
- The registry is intentionally thin (SQL + JSON) to avoid schema migrations

---

*Plan created: 2026-05-09*
*Test baseline: 983 passing*
*Target: 1000+ passing with all 8 preference types*

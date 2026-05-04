# Smart Approval Scoping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce repetitive security prompts for read-only commands by implementing path-hierarchy-aware global approvals.

**Architecture:** A centralized `ApprovalStore` manages `~/.vibe/approvals.json`, generalizing "Always" choices for a predefined list of "Safe" commands based on the directory tree.

**Tech Stack:** Python, `shlex`, `filelock`, `pathlib`.

---

### Task 1: Foundation - `ApprovalStore`

**Files:**
- Create: `vibe/tools/security/approval_store.py`
- Test: `tests/tools/security/test_approval_store.py`

- [ ] **Step 1: Write initial tests for ApprovalStore**

```python
import pytest
from pathlib import Path
from vibe.tools.security.approval_store import ApprovalStore

def test_is_safe_command():
    store = ApprovalStore(store_path=None) # InMemory for test
    assert store.is_safe_command("ls -la")
    assert store.is_safe_command("git status")
    assert not store.is_safe_command("rm -rf /")
    assert not store.is_safe_command("git push")

def test_path_scoping():
    store = ApprovalStore(store_path=None)
    store.add_scoped_approval("ls", "/work/project")
    
    assert store.check_approval("ls -la", "/work/project")
    assert store.check_approval("ls sub/dir", "/work/project")
    assert not store.check_approval("ls", "/other/project")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/tools/security/test_approval_store.py`

- [ ] **Step 3: Implement ApprovalStore**

```python
import json
import os
import shlex
from pathlib import Path
from typing import Any

SAFE_COMMANDS = {
    "ls", "find", "pwd", "du", "df", "stat",
    "cat", "head", "tail", "grep", "sort", "uniq", "wc", "jq"
}

SAFE_GIT_SUBCOMMANDS = {"status", "log", "diff", "branch", "show", "remote"}

class ApprovalStore:
    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path
        self.approvals = []
        if self.store_path and self.store_path.exists():
            self._load()

    def _load(self):
        try:
            with open(self.store_path, "r") as f:
                data = json.load(f)
                self.approvals = data.get("approvals", [])
        except (json.JSONDecodeError, IOError):
            self.approvals = []

    def _save(self):
        if not self.store_path: return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, "w") as f:
            json.dump({"version": "1.0", "approvals": self.approvals}, f, indent=2)

    def is_safe_command(self, command_line: str) -> bool:
        try:
            tokens = shlex.split(command_line)
        except ValueError: return False
        if not tokens: return False
        
        base = tokens[0]
        if base in SAFE_COMMANDS: return True
        
        if base == "git" and len(tokens) > 1:
            return tokens[1] in SAFE_GIT_SUBCOMMANDS
            
        if base == "python" and "-m" in tokens and "json.tool" in tokens:
            return True
            
        return False

    def add_scoped_approval(self, base_cmd: str, root_path: str):
        abs_root = str(Path(root_path).resolve())
        self.approvals.append({
            "type": "scoped_base_cmd",
            "command": base_cmd,
            "root_path": abs_root,
            "granted_at": "2026-05-03T00:00:00Z" # Placeholder
        })
        self._save()

    def add_exact_approval(self, command_line: str):
        self.approvals.append({
            "type": "exact_match",
            "command": command_line,
            "granted_at": "2026-05-03T00:00:00Z"
        })
        self._save()

    def check_approval(self, command_line: str, cwd: str) -> bool:
        abs_cwd = str(Path(cwd).resolve())
        tokens = shlex.split(command_line)
        if not tokens: return False
        base = tokens[0]

        for app in self.approvals:
            if app["type"] == "exact_match":
                if app["command"] == command_line: return True
            elif app["type"] == "scoped_base_cmd":
                if app["command"] == base:
                    if abs_cwd.startswith(app["root_path"]):
                        return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

- [ ] **Step 5: Commit**

### Task 2: HumanApprover Integration

**Files:**
- Modify: `vibe/tools/security/human_approval.py`

- [ ] **Step 1: Inject ApprovalStore into HumanApprover**
- [ ] **Step 2: Update request_approval to check store**
- [ ] **Step 3: Update choice logic to persist approvals**

### Task 3: Coordinator Context Passing

**Files:**
- Modify: `vibe/core/coordinators.py`

- [ ] **Step 1: Pass cwd to request_approval call**

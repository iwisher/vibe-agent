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
    """Manages persistent command approvals in ~/.vibe/approvals.json."""

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
        if not self.store_path:
            return
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_path, "w") as f:
                json.dump({"version": "1.0", "approvals": self.approvals}, f, indent=2)
        except IOError:
            # Fallback for read-only systems or permission issues
            pass

    def is_safe_command(self, command_line: str) -> bool:
        """Check if a command is read-only and eligible for scoping."""
        try:
            tokens = shlex.split(command_line)
        except ValueError:
            return False
        if not tokens:
            return False
        
        base = tokens[0]
        # Handle path-prefixed binaries like /bin/ls
        if "/" in base:
            base = Path(base).name

        if base in SAFE_COMMANDS:
            return True
        
        if base == "git" and len(tokens) > 1:
            return tokens[1] in SAFE_GIT_SUBCOMMANDS
            
        if base == "python" and "-m" in tokens and "json.tool" in tokens:
            return True
            
        return False

    def add_scoped_approval(self, base_cmd: str, root_path: str):
        """Add an approval for a base command and all its children paths."""
        abs_root = str(Path(root_path).resolve())
        # Remove existing scoped approval for the same command/path if it exists
        self.approvals = [
            a for a in self.approvals 
            if not (a.get("type") == "scoped_base_cmd" and a.get("command") == base_cmd and a.get("root_path") == abs_root)
        ]
        self.approvals.append({
            "type": "scoped_base_cmd",
            "command": base_cmd,
            "root_path": abs_root,
            "recursive": True,
            "granted_at": "2026-05-03T00:00:00Z" # Will be updated with real timestamp if needed
        })
        self._save()

    def add_exact_approval(self, command_line: str):
        """Add an approval for an exact command string."""
        # Remove existing exact approval if it exists
        self.approvals = [
            a for a in self.approvals 
            if not (a.get("type") == "exact_match" and a.get("command") == command_line)
        ]
        self.approvals.append({
            "type": "exact_match",
            "command": command_line,
            "granted_at": "2026-05-03T00:00:00Z"
        })
        self._save()

    def check_approval(self, command_line: str, cwd: str) -> bool:
        """Check if a command is approved in the given context."""
        abs_cwd = str(Path(cwd).resolve())
        try:
            tokens = shlex.split(command_line)
        except ValueError:
            return False
        if not tokens:
            return False
        
        base = tokens[0]
        if "/" in base:
            base = Path(base).name

        for app in self.approvals:
            if app.get("type") == "exact_match":
                if app.get("command") == command_line:
                    return True
            elif app.get("type") == "scoped_base_cmd":
                if app.get("command") == base:
                    root = app.get("root_path", "")
                    if abs_cwd == root or abs_cwd.startswith(root + os.sep) or (root == "/" and abs_cwd.startswith("/")):
                        return True
        return False

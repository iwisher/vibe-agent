import json
import os
import shlex
from pathlib import Path
from typing import Any

DEFAULT_STORE_PATH = Path.home() / ".vibe" / "approvals.json"

SAFE_COMMANDS = {
    "ls", "find", "pwd", "du", "df", "stat",
    "cat", "head", "tail", "grep", "sort", "uniq", "wc", "jq"
}

SAFE_GIT_SUBCOMMANDS = {"status", "log", "diff", "branch", "show", "remote"}

class ApprovalStore:
    """Manages persistent command approvals in ~/.vibe/approvals.json."""

    def __init__(self, store_path: Path | None = None):
        self.store_path = store_path or DEFAULT_STORE_PATH
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

    def _split_command_chain(self, command_line: str) -> list[dict[str, Any]]:
        """Split a command line into units and redirections.
        
        Returns a list of dicts: {'type': 'cmd'|'redirect', 'content': str}
        """
        try:
            tokens = shlex.split(command_line)
        except ValueError:
            return []

        units = []
        current_unit = []
        
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in {"|", "&&", ";", "||"}:
                if current_unit:
                    units.append({"type": "cmd", "content": " ".join(current_unit)})
                    current_unit = []
            elif token in {">", ">>", "2>", "2>>"}:
                if current_unit:
                    units.append({"type": "cmd", "content": " ".join(current_unit)})
                    current_unit = []
                if i + 1 < len(tokens):
                    units.append({"type": "redirect", "content": tokens[i+1]})
                    i += 1
            else:
                current_unit.append(token)
            i += 1
        
        if current_unit:
            units.append({"type": "cmd", "content": " ".join(current_unit)})
            
        return units

    def check_approval(self, command_line: str, cwd: str) -> bool:
        """Check if a command is approved in the given context."""
        abs_cwd = str(Path(cwd).resolve())
        
        # 1. Exact match check (fast path)
        for app in self.approvals:
            if app.get("type") == "exact_match" and app.get("command") == command_line:
                return True

        # 2. Split into units for chain verification
        units = self._split_command_chain(command_line)
        if not units:
            return False

        # If it's a simple command, check it directly
        if len(units) == 1 and units[0]["type"] == "cmd":
            return self._check_single_unit_approval(units[0]["content"], abs_cwd)

        # For chains, EVERY unit must be approved
        for unit in units:
            if unit["type"] == "cmd":
                if not self._check_single_unit_approval(unit["content"], abs_cwd):
                    return False
            elif unit["type"] == "redirect":
                if not self._is_path_in_hierarchy(unit["content"], abs_cwd):
                    return False
        
        return True

    def _check_single_unit_approval(self, command: str, abs_cwd: str) -> bool:
        """Check if a single command unit is safe or approved."""
        try:
            tokens = shlex.split(command)
        except ValueError:
            return False
        if not tokens:
            return False
        
        base = tokens[0]
        if "/" in base:
            base = Path(base).name

        # Check if it's a safe command
        if self.is_safe_command(command):
            # Safe commands are auto-approved if we are in an approved hierarchy for that base cmd
            # or if it's globally safe (implied by being in SAFE_COMMANDS)
            # Actually, the user wants "if all cmd in piped cmd are approved".
            # So we check if the base cmd is approved for this hierarchy.
            for app in self.approvals:
                if app.get("type") == "scoped_base_cmd" and app.get("command") == base:
                    root = app.get("root_path", "")
                    if self._is_path_in_hierarchy(abs_cwd, root):
                        return True
        
        # Also check for exact matches of this specific unit (less common but possible)
        for app in self.approvals:
            if app.get("type") == "exact_match" and app.get("command") == command:
                return True
                
        return False

    def _is_path_in_hierarchy(self, target_path: str, root_path: str) -> bool:
        """Check if target_path is within the root_path hierarchy."""
        try:
            abs_target = str(Path(target_path).resolve())
            abs_root = str(Path(root_path).resolve())
            return abs_target == abs_root or abs_target.startswith(abs_root + os.sep) or (abs_root == "/" and abs_target.startswith("/"))
        except Exception:
            return False

#!/usr/bin/env python3
"""PostToolUse hook: lint edited Python files with ruff.

Observation-only (always exits 0; hooks are fail-open by design).
Supports both Antigravity (.agents/hooks.json) and Kimi Code (user config).
Scoped to the vibe-agent checkout containing this script (worktrees included);
exits silently for any other project.

In Antigravity: prints diagnostics to stderr and outputs {} to stdout.
In Kimi Code: prints findings to stdout so they are appended to context.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUFF = os.path.join(REPO_ROOT, ".venv", "bin", "ruff")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    is_antigravity = "conversationId" in payload or "workspacePaths" in payload

    cwd = payload.get("cwd") or ""
    if not cwd and payload.get("workspacePaths"):
        cwd = payload["workspacePaths"][0]

    try:
        if cwd and os.path.commonpath([cwd, REPO_ROOT]) != REPO_ROOT:
            if is_antigravity:
                print("{}")
            return 0  # different project
    except ValueError:
        if is_antigravity:
            print("{}")
        return 0  # different drive / incomparable paths

    tool_call = payload.get("toolCall") or {}
    tool_args = tool_call.get("args") or {}
    path = (
        (payload.get("tool_input") or {}).get("path")
        or tool_args.get("TargetFile")
        or tool_args.get("target_file")
        or ""
    )

    if not path.endswith(".py"):
        if is_antigravity:
            print("{}")
        return 0
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    if not os.path.isfile(path):
        if is_antigravity:
            print("{}")
        return 0

    rel = os.path.relpath(path, REPO_ROOT)
    # Check if within vibe, tests, or scripts (including worktree paths)
    rel_parts = rel.split(os.sep)
    if rel_parts[0] == ".worktrees" and len(rel_parts) > 2:
        rel_top = rel_parts[2]
    else:
        rel_top = rel_parts[0]

    if rel_top not in ("vibe", "tests", "scripts"):
        if is_antigravity:
            print("{}")
        return 0

    cmd = [RUFF, "check", path] if os.path.isfile(RUFF) else ["ruff", "check", path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except Exception:
        if is_antigravity:
            print("{}")
        return 0

    if proc.returncode != 0:
        out = (proc.stdout or proc.stderr).strip()
        if out:
            msg = f"[vibe-agent hook] ruff check {rel}:\n{out[:2000]}"
            if is_antigravity:
                print(msg, file=sys.stderr)
            else:
                print(msg)

    if is_antigravity:
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

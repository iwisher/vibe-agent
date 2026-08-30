#!/usr/bin/env python3
"""Deterministic git status, worktree, and commit graph inspector."""

import argparse
import json
import subprocess
import sys


def parse_worktrees() -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        worktrees = []
        current: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line[9:].strip()
            elif line.startswith("HEAD "):
                current["head"] = line[5:].strip()
            elif line.startswith("branch "):
                current["branch"] = line[7:].strip().replace("refs/heads/", "")
            elif line == "bare":
                current["bare"] = "true"
            elif line == "detached":
                current["detached"] = "true"
        if current:
            worktrees.append(current)
        return worktrees
    except Exception:
        return []


def get_commit_tree(limit: int = 15) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "log", "--graph", "--oneline", "--decorate", f"-n{limit}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line]
    except Exception:
        return []


def get_git_status(
    include_tree: bool = True, include_worktrees: bool = True, tree_limit: int = 15
) -> int:
    try:
        branch_proc = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, check=False
        )
        branch = branch_proc.stdout.strip() or "HEAD"

        status_proc = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=False
        )
        lines = [line for line in status_proc.stdout.splitlines() if line.strip()]

        staged = [entry[3:] for entry in lines if entry[0] in ("M", "A", "D", "R", "C")]
        unstaged = [entry[3:] for entry in lines if entry[1] in ("M", "D")]
        untracked = [entry[3:] for entry in lines if entry.startswith("??")]

        result: dict[str, object] = {
            "branch": branch,
            "clean": len(lines) == 0,
            "total_changed": len(lines),
            "staged_count": len(staged),
            "unstaged_count": len(unstaged),
            "untracked_count": len(untracked),
            "files": [entry[3:] for entry in lines[:20]],
        }

        if include_worktrees:
            result["worktrees"] = parse_worktrees()

        if include_tree:
            result["commit_tree"] = get_commit_tree(limit=tree_limit)

        print(json.dumps(result))
        return 0
    except Exception as e:
        print(json.dumps({"error": str(e), "clean": False, "branch": "unknown"}))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic git status and tree inspector.")
    parser.add_argument(
        "--tree", action="store_true", default=True, help="Include commit graph tree"
    )
    parser.add_argument(
        "--no-tree", dest="tree", action="store_false", help="Exclude commit graph tree"
    )
    parser.add_argument(
        "--worktrees", action="store_true", default=True, help="Include git worktrees"
    )
    parser.add_argument(
        "--no-worktrees",
        dest="worktrees",
        action="store_false",
        help="Exclude git worktrees",
    )
    parser.add_argument("--limit", type=int, default=15, help="Max commits in tree graph")
    args = parser.parse_args()
    return get_git_status(
        include_tree=args.tree, include_worktrees=args.worktrees, tree_limit=args.limit
    )


if __name__ == "__main__":
    sys.exit(main())

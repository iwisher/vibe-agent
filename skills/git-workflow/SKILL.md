+++
vibe_skill_version = "2.0.0"
id = "git-workflow"
name = "Git Workflow & Tree Inspector"
description = "Inspect git branch, working tree state, commit graph tree, and active git worktrees"
category = "development"
tags = ["git", "workflow", "git_tree", "worktree", "developer_tools"]

[trigger]
patterns = ["git status", "check git", "branch state", "git tree", "git worktree", "commit graph"]
required_tools = ["bash"]

[[variables]]
name = "limit"
type = "integer"
required = false
default = 15
minimum = 1
maximum = 100
description = "Maximum number of commits to include in the graph tree"

[[steps]]
id = "inspect"
description = "Run git status and tree inspection script and emit structured JSON"
tool = "bash"
script = "scripts/status_summary.py"
command = "--limit {{ limit }}"

[steps.verification]
exit_code = 0
json_has_keys = ["branch", "clean", "worktrees", "commit_tree"]
+++

# Git Workflow & Tree Inspector

## Overview
Deterministically inspects git repository status, branch topology, commit graph tree (`git log --graph`), and active linked worktrees (`git worktree list`).

## Steps

### Step 1: Inspect
**Script:** `scripts/status_summary.py`
**Tool:** bash
**Command:** `--limit {{ limit }}`

**Verification:** exit_code == 0 and JSON contains `branch`, `clean`, `worktrees`, and `commit_tree`.

+++
vibe_skill_version = "2.0.0"
id = "refactor-verifier"
name = "Refactoring & Contract Verifier"
description = "Verify Python AST syntax, cross-file imports, and function/class definitions across refactored modules"
category = "development"
tags = ["refactor", "ast", "python", "contract", "developer_tools"]

[trigger]
patterns = ["verify refactor", "check imports", "verify syntax", "check contract"]
required_tools = ["bash"]

[[variables]]
name = "paths"
type = "string"
required = true
pattern = "^[A-Za-z0-9_./ ,-]+$"
description = "Comma-separated list of file paths or directories to verify"

[[steps]]
id = "verify"
description = "Run AST and cross-import contract verification script"
tool = "bash"
script = "scripts/verify_refactor.py"
command = "--paths {{ paths }}"

[steps.verification]
exit_code = 0
json_has_keys = ["status", "files_scanned", "valid"]
+++

# Refactoring & Contract Verifier

## Overview
Deterministically parses and verifies Python AST structures, syntax integrity, and import references across multiple modified source files to prevent broken contracts during long-horizon refactoring.

## Steps

### Step 1: Verify
**Script:** `scripts/verify_refactor.py`
**Tool:** bash
**Command:** `--paths {{ paths }}`

**Verification:** exit_code == 0 and JSON contains `status`, `files_scanned`, and `valid`.

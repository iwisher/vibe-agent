+++
vibe_skill_version = "2.0.0"
id = "code-auditor"
name = "Code Auditor"
description = "Audit Python code for syntax errors and line length limits"
category = "development"
tags = ["code_quality", "linting", "developer_tools"]

[trigger]
patterns = ["audit code", "check syntax", "lint files"]
required_tools = ["bash"]

[[variables]]
name = "path"
type = "string"
required = false
default = "."
description = "Path to file or directory to audit"

[[variables]]
name = "max_line_length"
type = "integer"
required = false
default = 100
minimum = 50
maximum = 200
description = "Maximum permitted line length"

[[steps]]
id = "audit"
description = "Run deterministic code audit script and emit JSON summary"
tool = "bash"
script = "scripts/audit.py"
command = "{{ path }} --max-line-length {{ max_line_length }}"

[steps.verification]
exit_code = 0
json_has_keys = ["scanned_files", "issues_count"]
+++

# Code Auditor Skill

## Overview
Deterministically audits Python files in a directory or file for syntax errors and line length violations.

## Steps

### Step 1: Audit
**Script:** `scripts/audit.py`
**Tool:** bash
**Command:** `{{ path }} --max-line-length {{ max_line_length }}`

**Verification:** exit_code == 0 and JSON contains `scanned_files` and `issues_count`.

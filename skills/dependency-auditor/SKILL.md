+++
vibe_skill_version = "2.0.0"
id = "dependency-auditor"
name = "Dependency & Supply-Chain Auditor"
description = "Audit Python requirements and project dependencies for insecure install patterns, obfuscated code, and known risks"
category = "security"
tags = ["security", "audit", "dependencies", "supply_chain", "developer_tools"]

[trigger]
patterns = ["audit dependencies", "check requirements", "supply chain audit", "scan packages"]
required_tools = ["bash"]

[[variables]]
name = "file_path"
type = "string"
required = true
pattern = "^[A-Za-z0-9_./ -]+$"
description = "Path to requirements.txt, pyproject.toml, or setup.py"

[[steps]]
id = "audit"
description = "Scan dependency definitions and setup scripts for supply-chain risks"
tool = "bash"
script = "scripts/audit_deps.py"
command = "--file {{ file_path }}"

[steps.verification]
exit_code = 0
json_has_keys = ["status", "packages_scanned", "risk_count"]
+++

# Dependency & Supply-Chain Auditor

## Overview
Scans dependency declarations (`requirements.txt`, `pyproject.toml`, `setup.py`) to detect supply-chain risks, unpinned dependencies, suspicious git/HTTP URLs, and obfuscated install-time execution hooks.

## Steps

### Step 1: Audit
**Script:** `scripts/audit_deps.py`
**Tool:** bash
**Command:** `--file {{ file_path }}`

**Verification:** exit_code == 0 and JSON contains `status`, `packages_scanned`, and `risk_count`.

+++
vibe_skill_version = "2.0.0"
id = "log-analyst"
name = "Log Analyst & Anomaly Clusterer"
description = "Analyze large execution logs, cluster unique error signatures, and extract root-cause diagnostics"
category = "observability"
tags = ["logs", "observability", "troubleshooting", "clustering", "developer_tools"]

[trigger]
patterns = ["analyze log", "find error", "cluster exceptions", "log triage", "root cause"]
required_tools = ["bash"]

[[variables]]
name = "log_path"
type = "string"
required = true
pattern = "^[A-Za-z0-9_./ -]+$"
description = "Path to the log file to analyze"

[[steps]]
id = "analyze"
description = "Cluster log error signatures and emit root-cause summary"
tool = "bash"
script = "scripts/analyze_logs.py"
command = "--log {{ log_path }}"

[steps.verification]
exit_code = 0
json_has_keys = ["total_lines", "signatures_count", "top_signatures"]
+++

# Log Analyst & Anomaly Clusterer

## Overview
Scans large, noisy execution logs to identify, cluster, and rank unique error signatures and stack traces without overflowing the agent's context window.

## Steps

### Step 1: Analyze
**Script:** `scripts/analyze_logs.py`
**Tool:** bash
**Command:** `--log {{ log_path }}`

**Verification:** exit_code == 0 and JSON contains `total_lines`, `signatures_count`, and `top_signatures`.

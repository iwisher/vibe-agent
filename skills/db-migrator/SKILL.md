+++
vibe_skill_version = "2.0.0"
id = "db-migrator"
name = "Safe Database Migrator"
description = "Safely execute database migrations with automatic snapshot backup, dry-run validation, and rollback guarantee"
category = "database"
tags = ["database", "sqlite", "migration", "safety", "developer_tools"]

[trigger]
patterns = ["migrate db", "run migration", "database schema update", "alter table"]
required_tools = ["bash"]

[[variables]]
name = "db_path"
type = "string"
required = true
pattern = "^[A-Za-z0-9_./ -]+$"
description = "Path to the SQLite database file"

[[variables]]
name = "sql_script"
type = "string"
required = true
description = "Path to the SQL migration file to execute"

[[steps]]
id = "migrate"
description = "Execute safe database migration with automatic backup and invariant validation"
tool = "bash"
script = "scripts/migrate.py"
command = "--db {{ db_path }} --sql {{ sql_script }}"

[steps.verification]
exit_code = 0
json_has_keys = ["status", "backup_created", "rows_preserved", "applied"]
+++

# Safe Database Migrator

## Overview
Executes database migrations safely with automated transaction isolation, pre-migration snapshot backup, dry-run execution, and automatic rollback on constraint violations.

## Steps

### Step 1: Migrate
**Script:** `scripts/migrate.py`
**Tool:** bash
**Command:** `--db {{ db_path }} --sql {{ sql_script }}`

**Verification:** exit_code == 0 and JSON contains `status`, `backup_created`, `rows_preserved`, and `applied`.

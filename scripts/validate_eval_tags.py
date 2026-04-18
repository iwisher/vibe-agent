#!/usr/bin/env python3
"""Validate eval YAML files for required fields, tags, and uniqueness.

Run this in CI before merging:
    python scripts/validate_eval_tags.py

Exit code 0 = all valid, 1 = violations found.
"""

import sys
from pathlib import Path

import yaml


EVAL_DIR = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"
REQUIRED_KEYS = {"id", "tags", "subsystem", "difficulty", "category", "input", "expected"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
VALID_SUBSYSTEMS = {
    "query_loop",
    "planner",
    "compactor",
    "feedback",
    "mcp_bridge",
    "error_recovery",
    "tool_system",
}
VALID_CATEGORIES = {
    "bash",
    "file_ops",
    "math",
    "reasoning",
    "multi_step",
    "edge",
    "error_recovery",
    "tool_use",
    "meta",
    "general",
}


def validate() -> int:
    violations = []
    seen_ids = {}

    if not EVAL_DIR.exists():
        print(f"ERROR: Eval directory not found: {EVAL_DIR}")
        return 1

    yaml_files = sorted(EVAL_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"WARNING: No YAML files in {EVAL_DIR}")
        return 0

    for filepath in yaml_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            violations.append((filepath.name, f"YAML parse error: {e}"))
            continue

        # Required keys
        missing = REQUIRED_KEYS - set(data.keys())
        if missing:
            violations.append((filepath.name, f"Missing required keys: {sorted(missing)}"))

        # Unique ID
        case_id = data.get("id")
        if case_id:
            if case_id in seen_ids:
                violations.append(
                    (filepath.name, f"Duplicate id '{case_id}' (also in {seen_ids[case_id]})")
                )
            else:
                seen_ids[case_id] = filepath.name

        # Difficulty
        difficulty = data.get("difficulty")
        if difficulty and difficulty not in VALID_DIFFICULTIES:
            violations.append(
                (filepath.name, f"Invalid difficulty '{difficulty}', must be one of {VALID_DIFFICULTIES}")
            )

        # Subsystem
        subsystem = data.get("subsystem")
        if subsystem and subsystem not in VALID_SUBSYSTEMS:
            violations.append(
                (filepath.name, f"Unrecognized subsystem '{subsystem}', expected one of {VALID_SUBSYSTEMS}")
            )

        # Category
        category = data.get("category")
        if category and category not in VALID_CATEGORIES:
            violations.append(
                (filepath.name, f"Unrecognized category '{category}', expected one of {VALID_CATEGORIES}")
            )

        # Tags
        tags = data.get("tags")
        if tags is not None and not isinstance(tags, list):
            violations.append((filepath.name, f"'tags' must be a list, got {type(tags).__name__}"))

    # Report
    print(f"Eval Suite Validation Report")
    print(f"{'=' * 50}")
    print(f"Files checked: {len(yaml_files)}")
    print(f"Violations:    {len(violations)}")
    print()

    if violations:
        for filename, reason in violations:
            print(f"  ❌ {filename}: {reason}")
        print()
        print("Validation FAILED. Fix violations before merging.")
        return 1

    print("✅ All eval cases pass validation.")
    return 0


if __name__ == "__main__":
    sys.exit(validate())

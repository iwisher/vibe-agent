#!/usr/bin/env python3
"""Deterministic Python code auditor checking syntax and formatting issues."""

import argparse
import ast
import json
import sys
from pathlib import Path


def audit_path(target_path: str, max_line_length: int = 100) -> int:
    path = Path(target_path)
    if not path.exists():
        print(
            json.dumps(
                {"error": f"Path not found: {target_path}", "scanned_files": 0, "issues_count": 1}
            )
        )
        return 1

    files = [path] if path.is_file() else list(path.glob("**/*.py"))
    issues = []
    scanned = 0

    for py_file in files:
        if any(
            part.startswith(".") or part in ("__pycache__", "venv", ".venv")
            for part in py_file.parts
        ):
            continue
        scanned += 1
        try:
            content = py_file.read_text(encoding="utf-8")
        except Exception as e:
            issues.append(
                {"file": str(py_file), "line": 0, "type": "read_error", "message": str(e)}
            )
            continue

        try:
            ast.parse(content, filename=str(py_file))
        except SyntaxError as e:
            issues.append(
                {
                    "file": str(py_file),
                    "line": e.lineno or 0,
                    "type": "syntax_error",
                    "message": str(e),
                }
            )

        for idx, line in enumerate(content.splitlines(), start=1):
            if len(line) > max_line_length:
                issues.append(
                    {
                        "file": str(py_file),
                        "line": idx,
                        "type": "line_too_long",
                        "message": f"Line length {len(line)} > {max_line_length}",
                    }
                )

    result = {
        "target": target_path,
        "scanned_files": scanned,
        "issues_count": len(issues),
        "issues": issues[:50],
    }
    print(json.dumps(result))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Python source files.")
    parser.add_argument("path", nargs="?", default=".", help="Path to file or directory to audit")
    parser.add_argument(
        "--max-line-length", type=int, default=100, help="Max allowed line length"
    )
    args = parser.parse_args()
    return audit_path(args.path, args.max_line_length)


if __name__ == "__main__":
    sys.exit(main())

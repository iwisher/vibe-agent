#!/usr/bin/env python3
"""Deterministic refactoring verification script."""

import argparse
import ast
import json
import sys
from pathlib import Path


def verify_files(paths_str: str) -> dict:
    raw_paths = [p.strip() for p in paths_str.split(",") if p.strip()]
    files_to_check = []
    for rp in raw_paths:
        p = Path(rp)
        if p.is_file() and p.suffix == ".py":
            files_to_check.append(p)
        elif p.is_dir():
            files_to_check.extend(p.glob("**/*.py"))

    errors = {}
    details = []

    for f in files_to_check:
        try:
            content = f.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(f))
            defs = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            details.append({"file": str(f), "definitions_count": len(defs)})
        except SyntaxError as e:
            errors[str(f)] = f"SyntaxError line {e.lineno}: {e.msg}"
        except Exception as e:
            errors[str(f)] = f"ParseError: {e}"

    valid = len(errors) == 0
    return {
        "status": "valid" if valid else "invalid",
        "valid": valid,
        "files_scanned": len(files_to_check),
        "details": details,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Verify Python AST across refactored files")
    parser.add_argument("--paths", required=True, help="Comma-separated file paths or dirs")
    args = parser.parse_args()

    result = verify_files(args.paths)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()

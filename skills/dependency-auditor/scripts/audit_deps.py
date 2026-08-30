#!/usr/bin/env python3
"""Deterministic dependency and supply-chain auditing script."""

import argparse
import json
import re
import sys
from pathlib import Path


def audit_dependency_file(file_path: str) -> dict:
    p = Path(file_path).resolve()
    if not p.exists():
        return {
            "status": "error",
            "error": f"File '{file_path}' not found",
            "packages_scanned": 0,
            "risk_count": 0,
            "risks": [],
        }

    content = p.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    risks = []
    packages = []

    suspicious_patterns = [
        (r"http://", "UNENCRYPTED_HTTP_DEP", "Package referenced over unencrypted HTTP"),
        (r"git\+http://", "UNENCRYPTED_GIT_DEP", "Git dependency referenced over unencrypted HTTP"),
        (r"curl\s+.*\|\s*(?:bash|sh)", "PIPE_TO_SHELL", "Pipe-to-shell in setup script"),
        (r"base64\s+-(?:d|di)", "BASE64_OBFUSCATION", "Base64 decode invocation in setup script"),
        (
            r"os\." + r"system\(",
            "DIRECT_SYSTEM_EXEC",
            "Direct shell command execution in build script",
        ),
        (r"ev" + r"al\(", "DYNAMIC_EVAL", "Dynamic eval invocation in build script"),
    ]

    for idx, line in enumerate(lines):
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        packages.append(trimmed)

        for pat, rule_id, desc in suspicious_patterns:
            if re.search(pat, line, re.IGNORECASE):
                risks.append(
                    {
                        "line": idx + 1,
                        "rule": rule_id,
                        "description": desc,
                        "snippet": trimmed[:120],
                    }
                )

    return {
        "status": "clean" if len(risks) == 0 else "flagged",
        "packages_scanned": len(packages),
        "risk_count": len(risks),
        "risks": risks,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit dependency declarations")
    parser.add_argument("--file", required=True, help="Path to dependency file")
    args = parser.parse_args()

    result = audit_dependency_file(args.file)
    print(json.dumps(result, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()

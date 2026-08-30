#!/usr/bin/env python3
"""Deterministic log anomaly and error signature clustering script."""

import argparse
import json
import re
import sys
from pathlib import Path


def analyze_log_file(log_path: str) -> dict:
    p = Path(log_path).resolve()
    if not p.exists():
        return {
            "status": "error",
            "error": f"Log file '{log_path}' not found",
            "total_lines": 0,
            "signatures_count": 0,
            "top_signatures": [],
        }

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    error_patterns = [
        (r"Exception:?\s*(.*)", "EXCEPTION"),
        (r"Error:?\s*(.*)", "ERROR"),
        (r"FATAL:?\s*(.*)", "FATAL"),
        (r"CRITICAL:?\s*(.*)", "CRITICAL"),
        (r"Traceback \(most recent call last\):", "TRACEBACK"),
        (r"Panic:?\s*(.*)", "PANIC"),
        (r"HTTP\s+(4\d\d|5\d\d)", "HTTP_ERROR"),
    ]

    signatures = {}
    for idx, line in enumerate(lines):
        for pat, cat in error_patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                snippet = line.strip()[:140]
                sig = f"[{cat}] {snippet}"
                if sig not in signatures:
                    signatures[sig] = {"count": 1, "first_line": idx + 1}
                else:
                    signatures[sig]["count"] += 1
                break

    sorted_sigs = sorted(signatures.items(), key=lambda x: x[1]["count"], reverse=True)
    top_signatures = [
        {"signature": k, "count": v["count"], "first_seen_line": v["first_line"]}
        for k, v in sorted_sigs[:20]
    ]

    return {
        "status": "success",
        "total_lines": len(lines),
        "signatures_count": len(signatures),
        "top_signatures": top_signatures,
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze log files for error clusters")
    parser.add_argument("--log", required=True, help="Path to log file")
    args = parser.parse_args()

    result = analyze_log_file(args.log)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()

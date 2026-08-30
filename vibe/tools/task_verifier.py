"""Task verifier tool for long-horizon agent task verification."""

import ast
import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from vibe.tools.tool_system import Tool, ToolResult


class TaskVerifierTool(Tool):
    """Built-in tool for validating workspace state, AST, database schemas, and logs."""

    def __init__(self, root_dir: str | None = None):
        super().__init__(
            name="task_verifier",
            description=(
                "Verify long-horizon task integrity: check Python AST syntax, "
                "verify checksums, validate DB schemas, or cluster log error signatures."
            ),
        )
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd().resolve()

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["verify_ast", "verify_checksums", "verify_db_schema", "analyze_logs"],
                    "description": "Verification action to perform",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths for AST or checksum verification",
                },
                "expected_checksums": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Mapping of relative file path to expected SHA-256 hex digest",
                },
                "db_path": {
                    "type": "string",
                    "description": "Relative path to SQLite database for schema/row verification",
                },
                "required_tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of table names that must exist in the database",
                },
                "min_rows": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "description": "Mapping of table name to minimum expected row count",
                },
                "log_content": {
                    "type": "string",
                    "description": "Raw log text to analyze for error signatures and anomalies",
                },
                "log_path": {
                    "type": "string",
                    "description": "Path to log file to analyze",
                },
            },
            "required": ["action"],
        }

    def _resolve(self, path_str: str) -> Path:
        p = (self.root_dir / path_str).resolve()
        try:
            p.relative_to(self.root_dir)
        except ValueError:
            raise ValueError(f"Path '{path_str}' escapes safe root directory '{self.root_dir}'")
        return p

    async def execute(self, **kwargs) -> ToolResult:
        action = kwargs.get("action")
        if not action:
            return ToolResult(
                success=False, content=None, error="Missing required parameter 'action'"
            )

        try:
            if action == "verify_ast":
                return self._verify_ast(kwargs.get("files", []))
            elif action == "verify_checksums":
                return self._verify_checksums(
                    kwargs.get("files", []), kwargs.get("expected_checksums")
                )
            elif action == "verify_db_schema":
                return self._verify_db_schema(
                    kwargs.get("db_path", ""),
                    kwargs.get("required_tables", []),
                    kwargs.get("min_rows", {}),
                )
            elif action == "analyze_logs":
                return self._analyze_logs(kwargs.get("log_content"), kwargs.get("log_path"))
            else:
                return ToolResult(success=False, content=None, error=f"Unknown action '{action}'")
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))

    def _verify_ast(self, files: list[str]) -> ToolResult:
        if not files:
            return ToolResult(
                success=False, content=None, error="No files specified for AST verification"
            )

        errors = {}
        checked = []
        for file_path in files:
            target = self._resolve(file_path)
            if not target.exists():
                errors[file_path] = "File not found"
                continue

            try:
                code = target.read_text(encoding="utf-8")
                tree = ast.parse(code, filename=str(target))
                defs = [
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                ]
                imports = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for n in node.names:
                            imports.append(n.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)
                checked.append({"file": file_path, "definitions": defs, "imports": imports})
            except SyntaxError as e:
                errors[file_path] = f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
            except Exception as e:
                errors[file_path] = f"AST parse error: {e}"

        if errors:
            return ToolResult(
                success=False,
                content={"checked": checked, "errors": errors},
                error=f"AST verification failed for {len(errors)} file(s)",
            )

        return ToolResult(
            success=True,
            content={
                "status": "valid",
                "files_checked": len(checked),
                "details": checked,
            },
        )

    def _verify_checksums(self, files: list[str], expected: dict[str, str] | None) -> ToolResult:
        computed = {}
        mismatches = {}

        file_list = files or (list(expected.keys()) if expected else [])
        if not file_list:
            return ToolResult(
                success=False, content=None, error="No files provided for checksum verification"
            )

        for file_path in file_list:
            target = self._resolve(file_path)
            if not target.exists() or not target.is_file():
                mismatches[file_path] = {"error": "File does not exist"}
                continue

            content = target.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            computed[file_path] = digest

            if expected and file_path in expected:
                if expected[file_path].lower() != digest.lower():
                    mismatches[file_path] = {
                        "expected": expected[file_path],
                        "actual": digest,
                    }

        if mismatches:
            return ToolResult(
                success=False,
                content={"computed": computed, "mismatches": mismatches},
                error=f"Checksum verification failed for {len(mismatches)} file(s)",
            )

        return ToolResult(
            success=True,
            content={"status": "verified", "checksums": computed},
        )

    def _verify_db_schema(
        self, db_path: str, required_tables: list[str], min_rows: dict[str, int]
    ) -> ToolResult:
        if not db_path:
            return ToolResult(success=False, content=None, error="db_path is required")

        target = self._resolve(db_path)
        if not target.exists():
            return ToolResult(
                success=False, content=None, error=f"Database file not found: {db_path}"
            )

        conn = sqlite3.connect(target)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            existing_tables = {row[0] for row in cursor.fetchall()}

            missing_tables = [t for t in required_tables if t not in existing_tables]
            if missing_tables:
                return ToolResult(
                    success=False,
                    content={"existing_tables": list(existing_tables)},
                    error=f"Missing required table(s): {missing_tables}",
                )

            row_counts = {}
            insufficient_rows = {}
            for table, expected_min in (min_rows or {}).items():
                if table in existing_tables:
                    if not re.match(r"^[A-Za-z0-9_]+$", table):
                        return ToolResult(
                            success=False, content=None, error=f"Invalid table name '{table}'"
                        )
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    cnt = cursor.fetchone()[0]
                    row_counts[table] = cnt
                    if cnt < expected_min:
                        insufficient_rows[table] = {"actual": cnt, "expected_min": expected_min}

            if insufficient_rows:
                return ToolResult(
                    success=False,
                    content={"row_counts": row_counts, "violations": insufficient_rows},
                    error=f"Row count violations in table(s): {list(insufficient_rows.keys())}",
                )

            return ToolResult(
                success=True,
                content={
                    "status": "valid",
                    "tables": list(existing_tables),
                    "row_counts": row_counts,
                },
            )
        finally:
            conn.close()

    def _analyze_logs(self, log_content: str | None, log_path: str | None) -> ToolResult:
        text = log_content or ""
        if log_path:
            target = self._resolve(log_path)
            if target.exists():
                text = target.read_text(encoding="utf-8", errors="replace")

        if not text:
            return ToolResult(
                success=False, content=None, error="No log content or log path provided"
            )

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

        found_signatures = {}
        for idx, line in enumerate(lines):
            for pat, cat in error_patterns:
                m = re.search(pat, line, re.IGNORECASE)
                if m:
                    snippet = line.strip()[:150]
                    sig = f"[{cat}] {snippet}"
                    if sig not in found_signatures:
                        found_signatures[sig] = {"count": 1, "first_line": idx + 1}
                    else:
                        found_signatures[sig]["count"] += 1
                    break

        summary = {
            "total_lines": len(lines),
            "error_signature_count": len(found_signatures),
            "signatures": [
                {"signature": k, "occurrences": v["count"], "line_number": v["first_line"]}
                for k, v in sorted(
                    found_signatures.items(), key=lambda x: x[1]["count"], reverse=True
                )
            ],
        }

        return ToolResult(success=True, content=summary)

"""Tests for TaskVerifierTool."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from vibe.tools.task_verifier import TaskVerifierTool


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.mark.asyncio
async def test_task_verifier_ast_valid(tmp_workspace: Path):
    f = tmp_workspace / "valid.py"
    f.write_text("def hello(name: str) -> str:\n    return f'Hello, {name}'\n", encoding="utf-8")

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(action="verify_ast", files=["valid.py"])

    assert result.success is True
    assert result.content["status"] == "valid"
    assert result.content["files_checked"] == 1


@pytest.mark.asyncio
async def test_task_verifier_ast_invalid_syntax(tmp_workspace: Path):
    f = tmp_workspace / "bad.py"
    f.write_text("def bad_syntax(:\n    pass\n", encoding="utf-8")

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(action="verify_ast", files=["bad.py"])

    assert result.success is False
    assert "SyntaxError" in result.content["errors"]["bad.py"]


@pytest.mark.asyncio
async def test_task_verifier_checksums(tmp_workspace: Path):
    f1 = tmp_workspace / "file1.txt"
    content = b"sample binary or text content"
    f1.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(
        action="verify_checksums",
        files=["file1.txt"],
        expected_checksums={"file1.txt": digest},
    )

    assert result.success is True
    assert result.content["status"] == "verified"


@pytest.mark.asyncio
async def test_task_verifier_checksum_mismatch(tmp_workspace: Path):
    f1 = tmp_workspace / "file1.txt"
    f1.write_bytes(b"content")

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(
        action="verify_checksums",
        files=["file1.txt"],
        expected_checksums={"file1.txt": "wrongdigest"},
    )

    assert result.success is False
    assert "file1.txt" in result.content["mismatches"]


@pytest.mark.asyncio
async def test_task_verifier_db_schema(tmp_workspace: Path):
    db = tmp_workspace / "test.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE users (id INT, name TEXT);")
    conn.execute("INSERT INTO users VALUES (1, 'alice'), (2, 'bob');")
    conn.commit()
    conn.close()

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(
        action="verify_db_schema",
        db_path="test.db",
        required_tables=["users"],
        min_rows={"users": 2},
    )

    assert result.success is True
    assert result.content["status"] == "valid"
    assert result.content["row_counts"]["users"] == 2


@pytest.mark.asyncio
async def test_task_verifier_analyze_logs(tmp_workspace: Path):
    log_text = (
        "2026-08-30 INFO [server] Starting up\n"
        "2026-08-30 ERROR [auth] InvalidTokenException: signature mismatch\n"
        "2026-08-30 ERROR [auth] InvalidTokenException: signature mismatch\n"
        "2026-08-30 CRITICAL [db] ConnectionRefusedError: pool down\n"
    )
    log_file = tmp_workspace / "app.log"
    log_file.write_text(log_text, encoding="utf-8")

    tool = TaskVerifierTool(root_dir=str(tmp_workspace))
    result = await tool.execute(action="analyze_logs", log_path="app.log")

    assert result.success is True
    assert result.content["total_lines"] == 4
    assert result.content["error_signature_count"] == 2

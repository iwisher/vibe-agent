"""Tests for scripts/validate_eval_tags.py."""

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


VALID_EVAL = {
    "id": "test-001",
    "tags": ["test"],
    "subsystem": "query_loop",
    "difficulty": "easy",
    "category": "general",
    "input": {"prompt": "hi"},
    "expected": {"response_contains": "hello"},
}


def run_validator(evals_dir: Path):
    script = Path(__file__).parent.parent / "scripts" / "validate_eval_tags.py"
    env = {"EVAL_DIR_OVERRIDE": str(evals_dir)}
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
        env={**dict(__import__("os").environ), **env},
    )
    return result


def test_validator_passes_on_valid_eval():
    with tempfile.TemporaryDirectory() as tmp:
        eval_dir = Path(tmp) / "builtin"
        eval_dir.mkdir()
        with open(eval_dir / "valid_001.yaml", "w") as f:
            yaml.dump(VALID_EVAL, f)

        # Monkeypatch EVAL_DIR inside the script by copying script and modifying
        script_src = Path(__file__).parent.parent / "scripts" / "validate_eval_tags.py"
        script_copy = Path(tmp) / "validate.py"
        content = script_src.read_text()
        content = content.replace(
            'EVAL_DIR = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"',
            f'EVAL_DIR = Path("{eval_dir}")'
        )
        script_copy.write_text(content)

        result = subprocess.run([sys.executable, str(script_copy)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "All eval cases pass validation" in result.stdout


def test_validator_fails_on_missing_keys():
    with tempfile.TemporaryDirectory() as tmp:
        eval_dir = Path(tmp) / "builtin"
        eval_dir.mkdir()
        bad = {k: v for k, v in VALID_EVAL.items() if k != "difficulty"}
        with open(eval_dir / "bad_001.yaml", "w") as f:
            yaml.dump(bad, f)

        script_src = Path(__file__).parent.parent / "scripts" / "validate_eval_tags.py"
        script_copy = Path(tmp) / "validate.py"
        content = script_src.read_text()
        content = content.replace(
            'EVAL_DIR = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"',
            f'EVAL_DIR = Path("{eval_dir}")'
        )
        script_copy.write_text(content)

        result = subprocess.run([sys.executable, str(script_copy)], capture_output=True, text=True)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Missing required keys" in result.stdout


def test_validator_fails_on_invalid_difficulty():
    with tempfile.TemporaryDirectory() as tmp:
        eval_dir = Path(tmp) / "builtin"
        eval_dir.mkdir()
        bad = {**VALID_EVAL, "difficulty": "impossible"}
        with open(eval_dir / "bad_001.yaml", "w") as f:
            yaml.dump(bad, f)

        script_src = Path(__file__).parent.parent / "scripts" / "validate_eval_tags.py"
        script_copy = Path(tmp) / "validate.py"
        content = script_src.read_text()
        content = content.replace(
            'EVAL_DIR = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"',
            f'EVAL_DIR = Path("{eval_dir}")'
        )
        script_copy.write_text(content)

        result = subprocess.run([sys.executable, str(script_copy)], capture_output=True, text=True)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Invalid difficulty" in result.stdout


def test_validator_fails_on_duplicate_id():
    with tempfile.TemporaryDirectory() as tmp:
        eval_dir = Path(tmp) / "builtin"
        eval_dir.mkdir()
        with open(eval_dir / "a_001.yaml", "w") as f:
            yaml.dump(VALID_EVAL, f)
        with open(eval_dir / "b_001.yaml", "w") as f:
            yaml.dump(VALID_EVAL, f)

        script_src = Path(__file__).parent.parent / "scripts" / "validate_eval_tags.py"
        script_copy = Path(tmp) / "validate.py"
        content = script_src.read_text()
        content = content.replace(
            'EVAL_DIR = Path(__file__).parent.parent / "vibe" / "evals" / "builtin"',
            f'EVAL_DIR = Path("{eval_dir}")'
        )
        script_copy.write_text(content)

        result = subprocess.run([sys.executable, str(script_copy)], capture_output=True, text=True)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "Duplicate id" in result.stdout

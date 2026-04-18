"""Eval store and runner for harness hill-climbing."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class EvalCase:
    id: str
    tags: List[str]
    input: Dict[str, Any]
    expected: Dict[str, Any]
    optimization_set: bool = True
    holdout_set: bool = False


@dataclass
class EvalResult:
    eval_id: str
    passed: bool
    diff: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    total_tokens: int = 0
    latency_seconds: float = 0.0


class EvalStore:
    """Loads evals from YAML and records results."""

    def __init__(self, db_path: Optional[str] = None, evals_dir: Optional[str] = None):
        self.db_path = db_path or str(Path.home() / ".vibe" / "memory" / "evals.db")
        self.evals_dir = evals_dir or str(Path(__file__).parent.parent.parent / "evals" / "builtin")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evals (
                    id TEXT PRIMARY KEY,
                    tags TEXT,
                    input TEXT,
                    expected TEXT,
                    optimization_set INTEGER,
                    holdout_set INTEGER
                );
                CREATE TABLE IF NOT EXISTS eval_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_id TEXT,
                    passed INTEGER,
                    diff TEXT,
                    timestamp TEXT,
                    total_tokens INTEGER,
                    latency_seconds REAL
                );
                """
            )
            # Schema migration: add missing columns for existing databases
            cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_results)")}
            if "total_tokens" not in cols:
                conn.execute("ALTER TABLE eval_results ADD COLUMN total_tokens INTEGER")
            if "latency_seconds" not in cols:
                conn.execute("ALTER TABLE eval_results ADD COLUMN latency_seconds REAL")

    def load_builtin_evals(self) -> List[EvalCase]:
        cases = []
        path = Path(self.evals_dir)
        if not path.exists():
            return cases
        for file in path.glob("*.yaml"):
            with open(file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            case_id = data.get("id", file.stem)
            tags = list(data.get("tags", []))
            # Validate required tags: subsystem, difficulty, category
            tag_names = {t.split("=")[0] if "=" in t else t for t in tags}
            if "subsystem" not in tag_names:
                tags.append(f"subsystem={data.get('subsystem', 'query_loop')}")
            if "difficulty" not in tag_names:
                tags.append(f"difficulty={data.get('difficulty', 'easy')}")
            if "category" not in tag_names:
                tags.append(f"category={data.get('category', 'general')}")
            cases.append(
                EvalCase(
                    id=case_id,
                    tags=tags,
                    input=data.get("input", {}),
                    expected=data.get("expected", {}),
                    optimization_set=data.get("optimization_set", True),
                    holdout_set=data.get("holdout_set", False),
                )
            )
        return cases

    def save_eval(self, case: EvalCase) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO evals (id, tags, input, expected, optimization_set, holdout_set) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case.id,
                    json.dumps(case.tags),
                    json.dumps(case.input),
                    json.dumps(case.expected),
                    int(case.optimization_set),
                    int(case.holdout_set),
                ),
            )

    def record_result(self, result: EvalResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO eval_results (eval_id, passed, diff, timestamp, total_tokens, latency_seconds) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result.eval_id,
                    int(result.passed),
                    json.dumps(result.diff),
                    result.timestamp,
                    result.total_tokens,
                    result.latency_seconds,
                ),
            )

    def get_results(self, eval_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if eval_id:
                rows = conn.execute(
                    "SELECT * FROM eval_results WHERE eval_id = ? ORDER BY timestamp DESC",
                    (eval_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM eval_results ORDER BY timestamp DESC").fetchall()
            return [dict(row) for row in rows]

    def summary(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM eval_results").fetchone()[0]
            passed = conn.execute("SELECT COUNT(*) FROM eval_results WHERE passed = 1").fetchone()[0]
            return {"total_runs": total, "passed": passed, "failed": total - passed, "score": passed / total if total else 0.0}

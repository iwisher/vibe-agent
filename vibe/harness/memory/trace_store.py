"""SQLite trace store for session logging."""

import json
import sqlite3
import pickle
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
except ImportError:
    SentenceTransformer = None
    np = None


class TraceStore:
    """Stores execution traces in SQLite."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(Path.home() / ".vibe" / "memory" / "traces.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    start_time TEXT,
                    end_time TEXT,
                    success INTEGER,
                    model TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    tool_calls TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    tool_name TEXT,
                    arguments TEXT,
                    result TEXT,
                    success INTEGER,
                    error TEXT,
                    duration_ms INTEGER,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                CREATE TABLE IF NOT EXISTS session_embeddings (
                    session_id TEXT PRIMARY KEY,
                    embedding BLOB,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );
                """
            )

    def _get_embedding(self, text: str) -> Any | None:
        """Get embedding for text using sentence-transformers all-MiniLM-L6-v2 with fallback."""
        if SentenceTransformer is None or np is None:
            return None
        if self._model is None:
            try:
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                return None
        try:
            return self._model.encode(text)
        except Exception:
            return None

    def log_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        success: bool,
        model: str,
        error: str | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, start_time, end_time, success, model, error) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, now, now, int(success), model, error),
            )
            
            all_content = []
            for msg in messages:
                content = msg.get("content") or ""
                all_content.append(content)
                conn.execute(
                    "INSERT INTO messages (session_id, role, content, tool_calls, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (
                        session_id,
                        msg.get("role"),
                        content,
                        json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
                        now,
                    ),
                )
            
            # Compute and store embedding
            combined_text = " ".join(all_content)
            emb = self._get_embedding(combined_text)
            if emb is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO session_embeddings (session_id, embedding) VALUES (?, ?)",
                    (session_id, pickle.dumps(emb)),
                )

            for tr in tool_results:
                conn.execute(
                    "INSERT INTO tool_calls (session_id, tool_name, arguments, result, success, error, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        tr.get("tool_name"),
                        json.dumps(tr.get("arguments")),
                        json.dumps(tr.get("result")),
                        int(tr.get("success", False)),
                        tr.get("error"),
                        tr.get("duration_ms", 0),
                    ),
                )

    def get_similar_sessions_vector(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve sessions using vector similarity."""
        query_emb = self._get_embedding(query)
        if query_emb is None or np is None:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT se.session_id, se.embedding, s.start_time, s.success, s.model 
                FROM session_embeddings se 
                JOIN sessions s ON se.session_id = s.id
                """
            ).fetchall()
            
            results = []
            for row in rows:
                sid = row["session_id"]
                try:
                    emb = pickle.loads(row["embedding"])
                    # Cosine similarity
                    norm_query = np.linalg.norm(query_emb)
                    norm_emb = np.linalg.norm(emb)
                    if norm_query > 0 and norm_emb > 0:
                        score = float(np.dot(query_emb, emb) / (norm_query * norm_emb))
                    else:
                        score = 0.0
                    
                    results.append({
                        "id": sid,
                        "start_time": row["start_time"],
                        "success": bool(row["success"]),
                        "model": row["model"],
                        "score": score
                    })
                except Exception:
                    continue
            
            results.sort(key=lambda x: x["score"], reverse=True)
            # Use a threshold of 0.3 for similarity relevance
            return [r for r in results if r["score"] > 0.3][:limit]

    def get_similar_sessions(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve similar sessions, preferring vector search."""
        vector_results = self.get_similar_sessions_vector(query, limit)
        if vector_results:
            return [{k: v for k, v in r.items() if k != "score"} for r in vector_results]

        # Fallback to keyword search
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        if not keywords:
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = " OR ".join(["LOWER(m.content) LIKE ?"] * len(keywords))
            sql = f"""
                SELECT s.*, m.content as msg_content
                FROM sessions s
                JOIN messages m ON s.id = m.session_id
                WHERE {placeholders}
                ORDER BY s.start_time DESC
                LIMIT ?
            """
            params = [f"%{k}%" for k in keywords] + [limit * 10]
            rows = conn.execute(sql, params).fetchall()

            # Deduplicate by session_id and score by keyword overlap
            scored = {}
            for row in rows:
                sid = row["id"]
                content = (row["msg_content"] or "").lower()
                score = sum(1 for k in keywords if k in content)
                if sid not in scored or score > scored[sid]["score"]:
                    scored[sid] = {
                        "id": sid,
                        "start_time": row["start_time"],
                        "success": bool(row["success"]),
                        "model": row["model"],
                        "score": score,
                    }

        sorted_sessions = sorted(scored.values(), key=lambda x: x["score"], reverse=True)
        return [{k: v for k, v in s.items() if k != "score"} for s in sorted_sessions[:limit]]

    def get_sessions(self, limit: int = 100, success: bool | None = None) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM sessions"
            params: list[Any] = []
            if success is not None:
                sql += " WHERE success = ?"
                params.append(int(success))
            sql += " ORDER BY start_time DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

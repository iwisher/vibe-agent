"""Scalable trace store with multiple storage backends.

Supports:
- SQLite (default): Full-featured with embeddings and vector search
- JSON: Simple file-based storage
- Memory: In-memory for testing

Features:
- Configurable retention policy (max_entries, retention_days)
- Efficient querying with pagination
- Vector similarity search (SQLite backend)
- Keyword fallback search
"""

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None


class BaseTraceStore(ABC):
    """Abstract base class for trace stores."""

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact secrets from session data before persistence."""
        try:
            from vibe.harness.security.redactor import get_default_redactor
            redactor = get_default_redactor()
            return redactor.redact_dict(data)
        except ImportError:
            return data

    @abstractmethod
    def log_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        success: bool,
        model: str,
        error: str | None = None,
    ) -> None:
        """Log a session. Implementations should call _redact() on messages/tool_results."""
        pass

    @abstractmethod
    def get_similar_sessions(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Get sessions similar to query."""
        pass

    @abstractmethod
    def get_recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent sessions."""
        pass

    @abstractmethod
    def get_sessions(
        self, limit: int = 100, success: bool | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Get sessions with pagination."""
        pass

    @abstractmethod
    def cleanup_old_sessions(self, retention_days: int) -> int:
        """Remove sessions older than retention_days. Returns count removed."""
        pass

    @abstractmethod
    def count_sessions(self) -> int:
        """Return total session count."""
        pass


class SQLiteTraceStore(BaseTraceStore):
    """SQLite-backed trace store with vector similarity search."""

    def __init__(
        self,
        db_path: str | None = None,
        max_entries: int = 10000,
        retention_days: int = 30,
        cleanup_interval_seconds: int = 300,
    ):
        if db_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                db_path = str(Path(base) / "traces.db")
            else:
                db_path = str(Path.home() / ".vibe" / "memory" / "traces.db")
        self.db_path = db_path
        self.max_entries = max_entries
        self.retention_days = retention_days
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_time = 0.0
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
                CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(start_time);
                CREATE INDEX IF NOT EXISTS idx_sessions_success ON sessions(success);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                """
            )

    def _serialize_embedding(self, embedding: Any) -> bytes:
        """Serialize embedding to compact numpy bytes (replaces pickle)."""
        if np is None:
            # Fallback to pickle if numpy unavailable
            import pickle
            return pickle.dumps(embedding)
        arr = np.array(embedding, dtype=np.float32)
        return arr.tobytes()

    def _deserialize_embedding(self, data: bytes) -> list[float] | None:
        """Deserialize embedding from numpy bytes. Detects old pickle format."""
        if not data:
            return None
        if np is not None and len(data) % 4 == 0:
            # Try numpy float32 first (new format)
            try:
                arr = np.frombuffer(data, dtype=np.float32)
                return arr.tolist()
            except Exception:
                pass
        # Fallback: old pickle format
        try:
            import pickle
            result = pickle.loads(data)
            if isinstance(result, list):
                return result
        except Exception:
            pass
        return None
    def _get_embedding(self, text: str) -> Any | None:
        """Get embedding using sentence-transformers (MiniLM) as fallback.

        Uses all-MiniLM-L6-v2 (384-dim) when available. If not installed,
        returns None and vector search falls back to keyword search.
        """
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return None

        # Singleton model — loaded once per process
        if not hasattr(self, "_st_model"):
            self._st_model = None
        if self._st_model is None:
            try:
                self._st_model = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception:
                return None

        try:
            vec = self._st_model.encode(text, convert_to_numpy=True)
            return vec.tolist() if hasattr(vec, "tolist") else list(vec)
        except Exception:
            return None

    def _should_cleanup(self) -> bool:
        """Check if cleanup should run based on interval."""
        import time
        now = time.time()
        if now - self._last_cleanup_time >= self.cleanup_interval_seconds:
            self._last_cleanup_time = now
            return True
        return False

    def force_cleanup(self) -> None:
        """Force immediate cleanup (useful for tests)."""
        self._last_cleanup_time = 0.0
        self._enforce_retention()

    def _enforce_retention(self) -> None:
        """Enforce max_entries and retention_days limits."""
        if not self._should_cleanup():
            return

        # Remove old sessions first
        self.cleanup_old_sessions(self.retention_days)

        # Then enforce max_entries by removing oldest
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            if count > self.max_entries:
                to_remove = count - self.max_entries
                conn.execute(
                    """
                    DELETE FROM sessions WHERE id IN (
                        SELECT id FROM sessions ORDER BY start_time ASC LIMIT ?
                    )
                    """,
                    (to_remove,),
                )

    def log_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        success: bool,
        model: str,
        error: str | None = None,
    ) -> None:
        # Redact secrets before persistence
        safe_messages = [self._redact(m) for m in messages]
        safe_tool_results = [self._redact(t) for t in tool_results]
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO sessions (id, start_time, end_time, success, model, error) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, now, now, int(success), model, error),
            )

            all_content = []
            for msg in safe_messages:
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
                    (session_id, self._serialize_embedding(emb)),
                )

            for tr in safe_tool_results:
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

        self._enforce_retention()

    def get_similar_sessions_vector(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve sessions using vector similarity with keyword pre-filtering."""
        query_emb = self._get_embedding(query)
        if query_emb is None or np is None:
            return []

        # Pre-filter: keyword overlap to reduce vector search space
        keywords = set(w.lower() for w in query.split() if len(w) > 2)
        prefiltered_ids = set()
        if keywords:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                placeholders = " OR ".join(["LOWER(content) LIKE ?"] * len(keywords))
                sql = f"""
                    SELECT DISTINCT session_id FROM messages
                    WHERE {placeholders}
                """
                params = [f"%{k}%" for k in keywords]
                rows = conn.execute(sql, params).fetchall()
                prefiltered_ids = {r["session_id"] for r in rows}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if prefiltered_ids:
                placeholders = ",".join("?" * len(prefiltered_ids))
                sql = f"""
                    SELECT se.session_id, se.embedding, s.start_time, s.success, s.model
                    FROM session_embeddings se
                    JOIN sessions s ON se.session_id = s.id
                    WHERE se.session_id IN ({placeholders})
                """
                rows = conn.execute(sql, list(prefiltered_ids)).fetchall()
            else:
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
                emb = self._deserialize_embedding(row["embedding"])
                if emb is None:
                    continue
                try:
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

    def get_recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent sessions ordered by start time descending."""
        return self.get_sessions(limit=limit)

    def get_sessions(
        self, limit: int = 100, success: bool | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            sql = "SELECT * FROM sessions"
            params: list[Any] = []
            if success is not None:
                sql += " WHERE success = ?"
                params.append(int(success))
            sql += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def cleanup_old_sessions(self, retention_days: int) -> int:
        """Remove sessions older than retention_days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE start_time < ?", (cutoff,))
            return cursor.rowcount

    def count_sessions(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
            return result[0] if result else 0


class JSONTraceStore(BaseTraceStore):
    """JSON file-based trace store."""

    def __init__(
        self,
        file_path: str | None = None,
        max_entries: int = 10000,
        retention_days: int = 30,
        cleanup_interval_seconds: int = 300,
    ):
        if file_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                file_path = str(Path(base) / "traces.json")
            else:
                file_path = str(Path.home() / ".vibe" / "memory" / "traces.json")
        self.file_path = file_path
        self.max_entries = max_entries
        self.retention_days = retention_days
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_time = 0.0
        Path(self.file_path).parent.mkdir(parents=True, exist_ok=True)
        self._data: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = []

    def _save(self) -> None:
        """Atomic write: temp file + rename to avoid corruption on crash."""
        import os
        temp_path = self.file_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(temp_path, self.file_path)

    def _should_cleanup(self) -> bool:
        """Check if cleanup should run based on interval."""
        import time
        now = time.time()
        if now - self._last_cleanup_time >= self.cleanup_interval_seconds:
            self._last_cleanup_time = now
            return True
        return False

    def _enforce_retention(self) -> None:
        """Enforce max_entries and retention_days."""
        if not self._should_cleanup():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        self._data = [
            s for s in self._data
            if datetime.fromisoformat(s["start_time"]) > cutoff
        ]
        if len(self._data) > self.max_entries:
            self._data = self._data[-self.max_entries:]

    def log_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        success: bool,
        model: str,
        error: str | None = None,
    ) -> None:
        # Redact secrets before persistence
        safe_messages = [self._redact(m) for m in messages]
        safe_tool_results = [self._redact(t) for t in tool_results]
        session = {
            "id": session_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "model": model,
            "error": error,
            "messages": safe_messages,
            "tool_results": safe_tool_results,
        }
        # Replace existing session or append
        self._data = [s for s in self._data if s["id"] != session_id]
        self._data.append(session)
        self._enforce_retention()
        self._save()

    def get_similar_sessions(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Keyword-based similarity for JSON backend."""
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        if not keywords:
            return []

        scored = []
        for session in self._data:
            score = 0
            text = " ".join([
                m.get("content", "") for m in session.get("messages", [])
            ]).lower()
            for kw in keywords:
                if kw in text:
                    score += 1
            if score > 0:
                scored.append((score, session))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def get_recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.get_sessions(limit=limit)

    def get_sessions(
        self, limit: int = 100, success: bool | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        sessions = self._data
        if success is not None:
            sessions = [s for s in sessions if s["success"] == success]
        sessions = sessions[::-1]  # Most recent first
        return sessions[offset:offset + limit]

    def cleanup_old_sessions(self, retention_days: int) -> int:
        before = len(self._data)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        self._data = [
            s for s in self._data
            if datetime.fromisoformat(s["start_time"]) > cutoff
        ]
        self._save()
        return before - len(self._data)

    def count_sessions(self) -> int:
        return len(self._data)


class MemoryTraceStore(BaseTraceStore):
    """In-memory trace store for testing."""

    def __init__(
        self,
        max_entries: int = 1000,
        retention_days: int = 30,
        cleanup_interval_seconds: int = 300,
    ):
        self.max_entries = max_entries
        self.retention_days = retention_days
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self._last_cleanup_time = 0.0
        self._data: list[dict[str, Any]] = []

    def _should_cleanup(self) -> bool:
        """Check if cleanup should run based on interval."""
        import time
        now = time.time()
        if now - self._last_cleanup_time >= self.cleanup_interval_seconds:
            self._last_cleanup_time = now
            return True
        return False

    def _enforce_retention(self) -> None:
        """Enforce max_entries and retention_days."""
        if not self._should_cleanup():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        self._data = [
            s for s in self._data
            if datetime.fromisoformat(s["start_time"]) > cutoff
        ]
        if len(self._data) > self.max_entries:
            self._data = self._data[-self.max_entries:]

    def log_session(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        success: bool,
        model: str,
        error: str | None = None,
    ) -> None:
        # Redact secrets before persistence
        safe_messages = [self._redact(m) for m in messages]
        safe_tool_results = [self._redact(t) for t in tool_results]
        session = {
            "id": session_id,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "model": model,
            "error": error,
            "messages": safe_messages,
            "tool_results": safe_tool_results,
        }
        self._data = [s for s in self._data if s["id"] != session_id]
        self._data.append(session)
        self._enforce_retention()

    def get_similar_sessions(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        keywords = [w.lower() for w in query.split() if len(w) > 2]
        if not keywords:
            return []

        scored = []
        for session in self._data:
            score = 0
            text = " ".join([
                m.get("content", "") for m in session.get("messages", [])
            ]).lower()
            for kw in keywords:
                if kw in text:
                    score += 1
            if score > 0:
                scored.append((score, session))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def get_recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.get_sessions(limit=limit)

    def get_sessions(
        self, limit: int = 100, success: bool | None = None, offset: int = 0
    ) -> list[dict[str, Any]]:
        sessions = self._data
        if success is not None:
            sessions = [s for s in sessions if s["success"] == success]
        sessions = sessions[::-1]
        return sessions[offset:offset + limit]

    def cleanup_old_sessions(self, retention_days: int) -> int:
        before = len(self._data)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        self._data = [
            s for s in self._data
            if datetime.fromisoformat(s["start_time"]) > cutoff
        ]
        return before - len(self._data)

    def count_sessions(self) -> int:
        return len(self._data)


def create_trace_store(
    storage_type: str = "sqlite",
    db_path: str | None = None,
    max_entries: int = 10000,
    retention_days: int = 30,
) -> BaseTraceStore:
    """Factory function to create the appropriate trace store."""
    if storage_type == "sqlite":
        return SQLiteTraceStore(db_path=db_path, max_entries=max_entries, retention_days=retention_days)
    elif storage_type == "json":
        return JSONTraceStore(file_path=db_path, max_entries=max_entries, retention_days=retention_days)
    elif storage_type == "memory":
        return MemoryTraceStore(max_entries=max_entries, retention_days=retention_days)
    else:
        raise ValueError(f"Unknown storage type: {storage_type}")


# Backward compatibility alias
TraceStore = SQLiteTraceStore

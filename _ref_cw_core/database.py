"""SQLite database for task persistence with single-writer queue support."""

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_config
from .models import Task, TaskStatus

# Import aiosqlite for proper async connection pooling
try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False


class TaskDatabase:
    """SQLite-based task storage for persistence (legacy synchronous version)."""

    def __init__(self, db_path: str = ".claudeworker/tasks.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT,
                    context TEXT,
                    metadata TEXT,
                    created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    parent_id TEXT,
                    tags TEXT,
                    priority INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON tasks(created_at)
            """)

    def _get_status_value(self, status) -> str:
        """Get the string value from a TaskStatus enum or string."""
        if isinstance(status, TaskStatus):
            return status.value
        return str(status)

    def save_task(self, task: Task) -> None:
        """Save or update a task."""
        status_value = self._get_status_value(task.status)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    task.id,
                    task.description,
                    status_value,
                    json.dumps(task.plan.model_dump() if task.plan else None, default=str),
                    json.dumps(task.context),
                    json.dumps(task.metadata),
                    task.created_at.isoformat() if task.created_at else None,
                    task.started_at.isoformat() if task.started_at else None,
                    task.completed_at.isoformat() if task.completed_at else None,
                    task.error_message,
                    task.parent_id,
                    json.dumps(task.tags),
                    task.priority,
                ),
            )

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()

            if not row:
                return None

            return self._row_to_task(row)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        """List tasks with optional filtering."""
        query = "SELECT * FROM tasks"
        params = []

        if status:
            query += " WHERE status = ?"
            params.append(status.value)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_task(row) for row in rows]

    def delete_task(self, task_id: str) -> bool:
        """Delete a task. Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def get_active_tasks(self) -> List[Task]:
        """Get all non-completed tasks."""
        active_statuses = [
            TaskStatus.PENDING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.PLANNED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.PAUSED.value,
        ]
        placeholders = ",".join(["?"] * len(active_statuses))

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at DESC",
                active_statuses,
            ).fetchall()
            return [self._row_to_task(row) for row in rows]

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert database row to Task object."""
        plan_data = json.loads(row[3]) if row[3] else None

        return Task(
            id=row[0],
            description=row[1],
            status=TaskStatus(row[2]),
            plan=plan_data,
            context=json.loads(row[4]) if row[4] else {},
            metadata=json.loads(row[5]) if row[5] else {},
            created_at=datetime.fromisoformat(row[6]) if row[6] else datetime.now(timezone.utc),
            started_at=datetime.fromisoformat(row[7]) if row[7] else None,
            completed_at=datetime.fromisoformat(row[8]) if row[8] else None,
            error_message=row[9],
            parent_id=row[10],
            tags=json.loads(row[11]) if row[11] else [],
            priority=row[12] if row[12] else 5,
        )


class ConnectionPool:
    """
    Async SQLite connection pool using aiosqlite.

    Maintains persistent connections for reads and writes to avoid
    connection creation overhead. Uses aiosqlite for true async support.
    """

    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = str(Path(db_path))
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._max_connections = max_connections
        self._pool: Optional["aiosqlite.Connection"] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the connection pool."""
        if not HAS_AIOSQLITE:
            raise RuntimeError(
                "aiosqlite is required for connection pooling. "
                "Install with: pip install aiosqlite"
            )

        async with self._lock:
            if self._initialized:
                return

            # Create primary connection with WAL mode
            self._pool = await aiosqlite.connect(self.db_path)

            # Enable WAL mode and optimizations
            await self._pool.execute("PRAGMA journal_mode=WAL")
            await self._pool.execute("PRAGMA synchronous=NORMAL")
            await self._pool.execute("PRAGMA temp_store=MEMORY")
            await self._pool.execute("PRAGMA cache_size=-64000")  # 64MB cache
            await self._pool.execute("PRAGMA mmap_size=268435456")  # 256MB mmap

            # Create tables if not exists
            await self._pool.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    plan TEXT,
                    context TEXT,
                    metadata TEXT,
                    created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    parent_id TEXT,
                    tags TEXT,
                    priority INTEGER
                )
            """)
            await self._pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
            """)
            await self._pool.execute("""
                CREATE INDEX IF NOT EXISTS idx_created ON tasks(created_at)
            """)
            await self._pool.commit()

            self._initialized = True

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        if not self._initialized:
            raise RuntimeError("Pool not initialized. Call initialize() first.")

        # For now, use the single shared connection (SQLite is single-writer)
        # In the future, this could support multiple read connections
        try:
            yield self._pool
        except Exception:
            # Re-raise exceptions for the caller to handle
            raise

    async def execute(self, sql: str, parameters: tuple = ()) -> "aiosqlite.Cursor":
        """Execute a query and return cursor."""
        async with self.acquire() as conn:
            return await conn.execute(sql, parameters)

    async def executemany(self, sql: str, parameters: List[tuple]) -> "aiosqlite.Cursor":
        """Execute many queries."""
        async with self.acquire() as conn:
            return await conn.executemany(sql, parameters)

    async def execute_script(self, sql: str) -> "aiosqlite.Cursor":
        """Execute a script."""
        async with self.acquire() as conn:
            return await conn.executescript(sql)

    async def fetchall(self, sql: str, parameters: tuple = ()) -> List[tuple]:
        """Execute query and fetch all results."""
        async with self.acquire() as conn:
            async with conn.execute(sql, parameters) as cursor:
                return await cursor.fetchall()

    async def fetchone(self, sql: str, parameters: tuple = ()) -> Optional[tuple]:
        """Execute query and fetch one result."""
        async with self.acquire() as conn:
            async with conn.execute(sql, parameters) as cursor:
                return await cursor.fetchone()

    async def commit(self) -> None:
        """Commit pending transactions."""
        if self._pool:
            await self._pool.commit()

    async def close(self) -> None:
        """Close all connections."""
        async with self._lock:
            if self._pool:
                await self._pool.close()
                self._pool = None
            self._initialized = False


class SingleWriterDatabase:
    """
    SQLite with single-writer queue + WAL mode + Connection Pooling.

    Uses aiosqlite for true async connection pooling, providing:
    - Persistent connections (no connection per operation)
    - True async/await (no thread pool overhead)
    - Better concurrency through WAL mode

    CRITICAL: All operations are now truly async using aiosqlite.
    """

    def __init__(self, db_path: str = ".claudeworker/tasks.db", max_workers: int = 1):
        self.db_path = str(Path(db_path))
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        # Keep executor for fallback or compatibility
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="db_writer")
        self._pool: Optional[ConnectionPool] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the database (must be called before use)."""
        async with self._lock:
            if self._initialized:
                return

            # Initialize connection pool with aiosqlite
            if HAS_AIOSQLITE:
                self._pool = ConnectionPool(self.db_path)
                await self._pool.initialize()
            else:
                # Fallback: use thread pool with sqlite3
                await asyncio.get_event_loop().run_in_executor(
                    self._executor, self._init_wal_mode_fallback
                )

            # Start single writer task for queue-based writes
            self._writer_task = asyncio.create_task(self._writer_loop())
            self._initialized = True

    def _init_wal_mode_fallback(self) -> None:
        """Initialize WAL mode for fallback mode (runs in thread pool)."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")

        # Create tables
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                plan TEXT,
                context TEXT,
                metadata TEXT,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                parent_id TEXT,
                tags TEXT,
                priority INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created ON tasks(created_at)
        """)
        conn.commit()
        conn.close()

    async def _writer_loop(self) -> None:
        """Single writer process - all writes go through here."""
        while True:
            op = await self._write_queue.get()
            if op is None:  # Shutdown signal
                break
            try:
                if HAS_AIOSQLITE and self._pool:
                    # Use connection pool for writes
                    cursor = await self._pool.execute(op['sql'], op['params'])
                    await self._pool.commit()
                    result = cursor.rowcount
                else:
                    # Fallback: use thread pool
                    result = await asyncio.get_event_loop().run_in_executor(
                        self._executor,
                        self._execute_write_fallback,
                        op['sql'],
                        op['params']
                    )
                op['future'].set_result(result)
            except Exception as e:
                op['future'].set_exception(e)

    def _execute_write_fallback(self, sql: str, params: tuple) -> int:
        """Execute write operation using fallback mode (runs in thread pool)."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount
        finally:
            conn.close()

    async def execute_write(self, sql: str, params: tuple) -> int:
        """Queue a write operation."""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        future = asyncio.get_event_loop().create_future()
        await self._write_queue.put({
            'sql': sql,
            'params': params,
            'future': future
        })
        return await future

    async def execute_read(self, sql: str, params: tuple = ()) -> List[tuple]:
        """Execute a read query using connection pool."""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        if HAS_AIOSQLITE and self._pool:
            # Use connection pool for reads (persistent connection)
            return await self._pool.fetchall(sql, params)
        else:
            # Fallback: use thread pool
            return await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._execute_read_fallback,
                sql,
                params
            )

    def _execute_read_fallback(self, sql: str, params: tuple) -> List[tuple]:
        """Synchronous read execution for fallback mode (runs in thread)."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA query_only=ON")
            cursor = conn.execute(sql, params)
            return cursor.fetchall()
        finally:
            conn.close()

    async def execute_read_in_transaction(self, sql: str, params: tuple = ()) -> List[tuple]:
        """Execute a read query within a transaction."""
        if not self._initialized:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        if HAS_AIOSQLITE and self._pool:
            # Use connection pool
            return await self._pool.fetchall(sql, params)
        else:
            # Fallback: use thread pool
            return await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._execute_read_fallback,
                sql,
                params
            )

    async def close(self) -> None:
        """Graceful shutdown."""
        if self._writer_task:
            await self._write_queue.put(None)
            await self._writer_task
        if self._pool:
            await self._pool.close()
        self._executor.shutdown(wait=True)
        self._initialized = False

    # Convenience methods for Task operations

    def _get_status_value(self, status) -> str:
        """Get the string value from a TaskStatus enum or string."""
        if isinstance(status, TaskStatus):
            return status.value
        return str(status)

    async def save_task(self, task: Task) -> None:
        """Save or update a task asynchronously."""
        status_value = self._get_status_value(task.status)
        await self.execute_write(
            """
            INSERT OR REPLACE INTO tasks VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                task.id,
                task.description,
                status_value,
                json.dumps(task.plan.model_dump() if task.plan else None, default=str),
                json.dumps(task.context),
                json.dumps(task.metadata),
                task.created_at.isoformat() if task.created_at else None,
                task.started_at.isoformat() if task.started_at else None,
                task.completed_at.isoformat() if task.completed_at else None,
                task.error_message,
                task.parent_id,
                json.dumps(task.tags),
                task.priority,
            ),
        )

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID asynchronously."""
        rows = await self.execute_read(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not rows:
            return None
        return self._row_to_task(rows[0])

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        """List tasks with optional filtering asynchronously."""
        query = "SELECT * FROM tasks"
        params = []

        if status:
            query += " WHERE status = ?"
            params.append(status.value)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self.execute_read(query, tuple(params))
        return [self._row_to_task(row) for row in rows]

    async def delete_task(self, task_id: str) -> bool:
        """Delete a task asynchronously. Returns True if deleted."""
        rowcount = await self.execute_write(
            "DELETE FROM tasks WHERE id = ?", (task_id,)
        )
        return rowcount > 0

    async def get_active_tasks(self) -> List[Task]:
        """Get all non-completed tasks asynchronously."""
        active_statuses = [
            TaskStatus.PENDING.value,
            TaskStatus.PLANNING.value,
            TaskStatus.PLANNED.value,
            TaskStatus.RUNNING.value,
            TaskStatus.PAUSED.value,
        ]
        placeholders = ",".join(["?"] * len(active_statuses))

        rows = await self.execute_read(
            f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY created_at DESC",
            tuple(active_statuses),
        )
        return [self._row_to_task(row) for row in rows]

    def _row_to_task(self, row: tuple) -> Task:
        """Convert database row to Task object."""
        plan_data = json.loads(row[3]) if row[3] else None

        return Task(
            id=row[0],
            description=row[1],
            status=TaskStatus(row[2]),
            plan=plan_data,
            context=json.loads(row[4]) if row[4] else {},
            metadata=json.loads(row[5]) if row[5] else {},
            created_at=datetime.fromisoformat(row[6]) if row[6] else datetime.now(timezone.utc),
            started_at=datetime.fromisoformat(row[7]) if row[7] else None,
            completed_at=datetime.fromisoformat(row[8]) if row[8] else None,
            error_message=row[9],
            parent_id=row[10],
            tags=json.loads(row[11]) if row[11] else [],
            priority=row[12] if row[12] else 5,
        )


# Factory function for getting the appropriate database instance
async def get_database(db_path: str = ".claudeworker/tasks.db") -> Any:
    """
    Get the appropriate database instance based on feature flags.

    Returns SingleWriterDatabase if CW_USE_SINGLE_WRITER is enabled,
    otherwise returns legacy TaskDatabase.
    """
    config = get_config()

    if config.features.use_single_writer:
        db = SingleWriterDatabase(
            db_path=db_path,
            max_workers=config.database.max_workers
        )
        await db.initialize()
        return db
    else:
        return TaskDatabase(db_path=db_path)

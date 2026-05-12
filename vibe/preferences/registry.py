"""Base preference registry with SQLite persistence."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vibe.preferences.models import PreferencePolicy, PreferenceRule


class PreferenceRegistry:
    """SQLite-backed registry for preference policies.

    Each domain gets its own policy table. Rules are JSON-serialized.

    NOTE: hit_count updates are batched in memory and flushed on session shutdown
    to avoid race conditions during parallel tool execution.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            base = os.environ.get("VIBE_MEMORY_DIR")
            if base:
                db_path = str(Path(base) / "preferences.db")
            else:
                db_path = str(Path.home() / ".vibe" / "memory" / "preferences.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._pending_hit_counts: dict[str, dict[str, int]] = {}  # domain -> {rule_id: count}

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS preference_policies (
                    domain TEXT PRIMARY KEY,
                    policy_json TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    updated_at TEXT
                );
            """)

    def save_policy(self, policy: PreferencePolicy) -> None:
        """Save or update a policy."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO preference_policies
                   (domain, policy_json, enabled, updated_at)
                   VALUES (?, ?, ?, datetime('now'))""",
                (policy.domain, json.dumps(policy.model_dump()), int(policy.enabled)),
            )

    def load_policy(self, domain: str) -> PreferencePolicy | None:
        """Load a policy by domain. Returns None if not found."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT policy_json FROM preference_policies WHERE domain = ?",
                (domain,),
            ).fetchone()
            if row is None:
                return None
            return PreferencePolicy(**json.loads(row[0]))

    def list_domains(self) -> list[str]:
        """List all domains with saved policies."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT domain FROM preference_policies WHERE enabled = 1"
            ).fetchall()
            return [r[0] for r in rows]

    def delete_policy(self, domain: str) -> bool:
        """Delete a policy. Returns True if deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM preference_policies WHERE domain = ?",
                (domain,),
            )
            return cursor.rowcount > 0

    def batch_hit(self, domain: str, rule_id: str) -> None:
        """Record a hit in memory (not persisted yet)."""
        if domain not in self._pending_hit_counts:
            self._pending_hit_counts[domain] = {}
        self._pending_hit_counts[domain][rule_id] = (
            self._pending_hit_counts[domain].get(rule_id, 0) + 1
        )

    def flush_hits(self) -> None:
        """Persist all pending hit counts to the database. Call on session shutdown."""
        if not self._pending_hit_counts:
            return

        for domain, hits in self._pending_hit_counts.items():
            policy = self.load_policy(domain)
            if policy is None:
                continue

            for rule in policy.rules:
                if rule.rule_id in hits:
                    rule.hit_count += hits[rule.rule_id]
                    rule.last_used_at = datetime.now(timezone.utc).isoformat()

            self.save_policy(policy)

        self._pending_hit_counts.clear()

    def prune_stale(self, days: int = 30) -> int:
        """Remove inferred rules not used in N days. Returns count removed."""
        from vibe.preferences.models import PreferenceSource

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        removed = 0

        for domain in self.list_domains():
            policy = self.load_policy(domain)
            if policy is None:
                continue

            original_len = len(policy.rules)
            policy.rules = [
                r
                for r in policy.rules
                if r.source != PreferenceSource.INFERRED
                or (r.last_used_at is not None and r.last_used_at >= cutoff)
            ]
            removed += original_len - len(policy.rules)
            self.save_policy(policy)

        return removed

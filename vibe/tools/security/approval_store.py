"""Durable approval store for vibe-agent.

JSON file at ~/.vibe/exec-approvals.json with 0o600 permissions.
Atomic write (temp+fsync+rename).
File locking via fcntl advisory lock.
Symlink rejection.
"""

import fcntl
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


@dataclass
class ApprovalEntry:
    """Single approval entry."""

    approval_type: str  # "pattern" or "command"
    key: str  # pattern_id or command_sha256
    timestamp: str
    approved_by: str  # "user" or "auto"
    notes: str = ""


class ApprovalStore:
    """Durable approval store with atomic writes and file locking."""

    def __init__(self, store_path: Optional[Path] = None):
        if store_path is None:
            store_path = Path.home() / ".vibe" / "exec-approvals.json"
        self._store_path = store_path
        self._ensure_store()

    def _ensure_store(self) -> None:
        """Create store directory and file if missing."""
        parent = self._store_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Set parent dir permissions to 0o700
        os.chmod(parent, 0o700)

        if not self._store_path.exists():
            self._store_path.write_text("[]")
            os.chmod(self._store_path, 0o600)

        # Check for symlinks (security)
        self._check_symlinks()

    def _check_symlinks(self) -> None:
        """Reject any symlink component in the store path."""
        path = self._store_path
        while path != path.parent:
            if path.is_symlink():
                raise PermissionError(
                    f"Approval store path contains symlink: {path}"
                )
            path = path.parent

    def _load(self) -> list[dict]:
        """Load approvals from file with read lock."""
        with open(self._store_path, "r") as f:
            # Advisory read lock
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                content = f.read()
                if not content.strip():
                    return []
                return json.loads(content)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _save(self, data: list[dict]) -> None:
        """Save approvals atomically with write lock."""
        # Write to temp file in same directory
        fd, temp_path = tempfile.mkstemp(
            dir=self._store_path.parent,
            prefix=".approvals.tmp.",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Set permissions before rename
            os.chmod(temp_path, 0o600)

            # Atomic rename
            os.rename(temp_path, self._store_path)

            # Sync directory to ensure rename is durable
            dir_fd = os.open(self._store_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def is_approved(self, approval_type: str, key: str) -> bool:
        """Check if a pattern or command is approved."""
        entries = self._load()
        for entry in entries:
            if entry.get("approval_type") == approval_type and entry.get("key") == key:
                return True
        return False

    def _load_exclusive(self) -> list[dict]:
        """Load approvals with exclusive lock for read-modify-write."""
        with open(self._store_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                content = f.read()
                if not content.strip():
                    return []
                return json.loads(content)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _save_locked(self, f, data: list[dict]) -> None:
        """Save approvals while holding the lock."""
        f.seek(0)
        f.truncate()
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    def add_approval(self, entry: ApprovalEntry) -> None:
        """Add an approval entry with exclusive lock."""
        with open(self._store_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                content = f.read()
                entries = json.loads(content) if content.strip() else []

                # Check for duplicates
                for existing in entries:
                    if (
                        existing.get("approval_type") == entry.approval_type
                        and existing.get("key") == entry.key
                    ):
                        return  # Already approved

                entries.append(asdict(entry))
                self._save_locked(f, entries)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def remove_approval(self, approval_type: str, key: str) -> bool:
        """Remove an approval entry with exclusive lock. Returns True if found."""
        with open(self._store_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                content = f.read()
                entries = json.loads(content) if content.strip() else []
                new_entries = [
                    e
                    for e in entries
                    if not (
                        e.get("approval_type") == approval_type and e.get("key") == key
                    )
                ]
                if len(new_entries) < len(entries):
                    self._save_locked(f, new_entries)
                    return True
                return False
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def list_approvals(self) -> list[ApprovalEntry]:
        """List all approval entries."""
        entries = self._load()
        return [ApprovalEntry(**e) for e in entries]

    def clear(self) -> None:
        """Clear all approvals with exclusive lock."""
        with open(self._store_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                self._save_locked(f, [])
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def hash_command(command: str) -> str:
        """Hash a command for storage."""
        return hashlib.sha256(command.encode("utf-8")).hexdigest()

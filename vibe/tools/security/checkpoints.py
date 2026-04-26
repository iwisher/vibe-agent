"""Checkpoints - rollback capability for security-sensitive operations.

Provides state capture and restoration for:
- File system operations
- Environment variables
- Working directory
"""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Optional


class CheckpointType(Enum):
    """Types of checkpoints."""
    FILE_OPERATION = "file_operation"
    ENV_CHANGE = "env_change"
    DIRECTORY_CHANGE = "directory_change"
    COMMAND_EXECUTION = "command_execution"


@dataclass
class FileState:
    """Captured state of a file."""
    path: str
    exists: bool
    content: Optional[str] = None
    permissions: Optional[int] = None
    backup_path: Optional[str] = None


@dataclass
class EnvState:
    """Captured state of environment variables."""
    added: dict[str, str]  # vars that were added
    modified: dict[str, tuple[str, str]]  # var -> (old, new)
    removed: list[str]  # vars that were removed


@dataclass
class Checkpoint:
    """A single checkpoint."""
    id: str
    timestamp: str
    type: str
    description: str
    file_states: list[FileState]
    env_state: Optional[EnvState]
    cwd: Optional[str]
    ttl_seconds: Optional[int] = None  # Time-to-live, None = permanent


class CheckpointManager:
    """Manages checkpoints for rollback capability."""

    def __init__(
        self,
        backup_dir: Optional[Path] = None,
        default_ttl: Optional[int] = 3600,  # 1 hour default
    ):
        if backup_dir is None:
            backup_dir = Path(tempfile.gettempdir()) / "vibe-checkpoints"
        self._backup_dir = backup_dir
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._default_ttl = default_ttl
        self._checkpoints: list[Checkpoint] = []
        self._checkpoint_index: dict[str, Checkpoint] = {}

    def create(
        self,
        checkpoint_type: CheckpointType,
        description: str,
        files: Optional[list[str]] = None,
        env_vars: Optional[list[str]] = None,
        ttl_seconds: Optional[int] = None,
    ) -> Checkpoint:
        """Create a checkpoint before a dangerous operation.

        Args:
            checkpoint_type: Type of operation being checkpointed
            description: Human-readable description
            files: List of file paths to capture
            env_vars: List of env var names to capture
            ttl_seconds: How long to keep this checkpoint (None = permanent)
        """
        checkpoint_id = f"cp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        # Use provided TTL or default (None = permanent)
        effective_ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        
        # Capture file states
        file_states = []
        if files:
            for file_path in files:
                file_states.append(self._capture_file_state(file_path))

        # Capture env state
        env_state = None
        if env_vars:
            env_state = self._capture_env_state(env_vars)

        # Capture working directory
        cwd = os.getcwd()

        checkpoint = Checkpoint(
            id=checkpoint_id,
            timestamp=datetime.now().isoformat(),
            type=checkpoint_type.value,
            description=description,
            file_states=file_states,
            env_state=env_state,
            cwd=cwd,
            ttl_seconds=effective_ttl,
        )

        self._checkpoints.append(checkpoint)
        self._checkpoint_index[checkpoint_id] = checkpoint
        
        # Clean up expired checkpoints
        self._cleanup_expired()
        
        return checkpoint

    def _capture_file_state(self, file_path: str) -> FileState:
        """Capture current state of a file."""
        path = Path(file_path)
        
        if not path.exists():
            return FileState(
                path=file_path,
                exists=False,
                content=None,
                permissions=None,
                backup_path=None,
            )

        # Create backup
        backup_name = f"{path.name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        backup_path = self._backup_dir / backup_name
        
        if path.is_file():
            shutil.copy2(path, backup_path)
            content = path.read_text() if path.stat().st_size < 1024 * 1024 else None  # Only capture small files
        else:
            content = None
            if path.is_dir():
                shutil.copytree(path, backup_path)

        return FileState(
            path=file_path,
            exists=True,
            content=content,
            permissions=path.stat().st_mode,
            backup_path=str(backup_path),
        )

    def _capture_env_state(self, env_vars: list[str]) -> EnvState:
        """Capture current state of environment variables."""
        added = {}
        modified = {}
        
        for var in env_vars:
            if var in os.environ:
                added[var] = os.environ[var]
        
        return EnvState(added=added, modified={}, removed=[])

    def rollback(self, checkpoint_id: str) -> bool:
        """Rollback to a checkpoint state.

        Returns True if successful.
        """
        checkpoint = self._checkpoint_index.get(checkpoint_id)
        if not checkpoint:
            return False

        # Restore files
        for file_state in checkpoint.file_states:
            self._restore_file_state(file_state)

        # Restore env vars
        if checkpoint.env_state:
            self._restore_env_state(checkpoint.env_state)

        # Restore working directory
        if checkpoint.cwd and os.path.exists(checkpoint.cwd):
            os.chdir(checkpoint.cwd)

        return True

    def _restore_file_state(self, file_state: FileState) -> None:
        """Restore a file to its checkpointed state."""
        path = Path(file_state.path)
        
        if not file_state.exists:
            # File didn't exist at checkpoint, remove it if it exists now
            if path.exists():
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            return

        # File existed at checkpoint
        if file_state.backup_path and os.path.exists(file_state.backup_path):
            # Restore from backup
            if path.exists():
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            
            backup = Path(file_state.backup_path)
            if backup.is_file():
                shutil.copy2(backup, path)
            elif backup.is_dir():
                shutil.copytree(backup, path)
            
            # Restore permissions
            if file_state.permissions:
                os.chmod(path, file_state.permissions)
        elif file_state.content is not None:
            # Restore from captured content
            path.write_text(file_state.content)
            if file_state.permissions:
                os.chmod(path, file_state.permissions)

    def _restore_env_state(self, env_state: EnvState) -> None:
        """Restore environment variables."""
        # Remove added vars
        for var in env_state.added:
            if var in os.environ:
                del os.environ[var]
        
        # Restore modified vars
        for var, (old_val, _) in env_state.modified.items():
            os.environ[var] = old_val
        
        # Restore removed vars
        for var in env_state.removed:
            # We don't know the old value, so we can't restore
            pass

    def delete(self, checkpoint_id: str) -> bool:
        """Delete a checkpoint and its backups."""
        checkpoint = self._checkpoint_index.get(checkpoint_id)
        if not checkpoint:
            return False

        # Clean up backup files
        for file_state in checkpoint.file_states:
            if file_state.backup_path and os.path.exists(file_state.backup_path):
                backup = Path(file_state.backup_path)
                if backup.is_file():
                    backup.unlink()
                elif backup.is_dir():
                    shutil.rmtree(backup)

        # Remove from tracking
        self._checkpoints.remove(checkpoint)
        del self._checkpoint_index[checkpoint_id]

        return True

    def list_checkpoints(self) -> list[Checkpoint]:
        """List all active checkpoints."""
        self._cleanup_expired()
        return list(self._checkpoints)

    def get(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get a specific checkpoint."""
        return self._checkpoint_index.get(checkpoint_id)

    def _cleanup_expired(self) -> None:
        """Remove expired checkpoints."""
        now = datetime.now()
        expired = []
        
        for cp in self._checkpoints:
            if cp.ttl_seconds is not None:
                created = datetime.fromisoformat(cp.timestamp)
                if (now - created).total_seconds() > cp.ttl_seconds:
                    expired.append(cp.id)
        
        for cp_id in expired:
            self.delete(cp_id)

    def clear_all(self) -> None:
        """Clear all checkpoints and backups."""
        for cp in list(self._checkpoints):
            self.delete(cp.id)
        
        # Clean up any remaining files in backup dir
        if self._backup_dir.exists():
            for item in self._backup_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

    def stats(self) -> dict:
        """Get checkpoint statistics."""
        self._cleanup_expired()
        total_size = 0
        for cp in self._checkpoints:
            for fs in cp.file_states:
                if fs.backup_path and os.path.exists(fs.backup_path):
                    path = Path(fs.backup_path)
                    if path.is_file():
                        total_size += path.stat().st_size
                    elif path.is_dir():
                        total_size += sum(
                            f.stat().st_size for f in path.rglob("*") if f.is_file()
                        )
        
        return {
            "count": len(self._checkpoints),
            "total_backup_size": total_size,
            "backup_dir": str(self._backup_dir),
        }

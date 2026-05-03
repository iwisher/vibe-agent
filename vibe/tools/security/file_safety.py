"""File safety module for vibe-agent.

Provides write denylist, read blocklist, path traversal hardening,
read loop detection, and cross-agent file locking.
"""

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ── Write Denylist ──────────────────────────────────────────────────────────

WRITE_DENYLIST_FILES: set[str] = {
    "~/.ssh/authorized_keys",
    "~/.ssh/id_rsa",
    "~/.ssh/id_ed25519",
    "~/.ssh/id_ecdsa",
    "~/.env",
    "~/.bashrc",
    "~/.zshrc",
    "~/.netrc",
    "~/.bash_profile",
    "~/.bash_login",
    "~/.profile",
    "/etc/sudoers",
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/gshadow",
    "/etc/hosts",
    "/etc/resolv.conf",
}

WRITE_DENYLIST_PREFIXES: tuple[str, ...] = (
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.kube",
    "/etc/sudoers.d",
    "/etc/systemd",
    "~/.docker",
    "~/.azure",
    "~/.config/gh",
    "~/.config/gcloud",
    "~/.config/helm",
    "~/.config/kubectl",
)

# ── Read Blocklist ───────────────────────────────────────────────────────────

READ_BLOCKLIST_FILES: set[str] = {
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/stdin",
    "/dev/tty",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/null",
    "/dev/full",
    "/dev/port",
    "/dev/kmem",
    "/dev/mem",
}

READ_BLOCKLIST_PREFIXES: tuple[str, ...] = (
    "/etc/",
    "/boot/",
    "/usr/lib/systemd/",
    "/private/etc/",
    "/private/var/",
    "/sys/",
    "/proc/",
)

# ── Path Traversal ──────────────────────────────────────────────────────────


class FileSafetyError(PermissionError):
    """Specific file safety violation with reason."""

    def __init__(self, reason: str, path: str):
        self.reason = reason
        self.path = path
        super().__init__(f"File safety violation ({reason}): {path}")


@dataclass
class ReadLoopState:
    """Tracks read loop detection state."""

    path: str = ""
    offset: int = 0
    limit: int = 0
    mtime: float = 0.0
    count: int = 0


class FileSafetyGuard:
    """File safety guard with denylist, blocklist, traversal checks."""

    def __init__(self, safe_root: Optional[Path] = None):
        self.safe_root = safe_root
        self._read_loop_state = ReadLoopState()

    # ── Write Denylist ──────────────────────────────────────────────────────

    def check_write(self, path: str | Path) -> None:
        """Check if write to path is allowed. Raises FileSafetyError if not."""
        path_str = self._normalize_path(path)

        # Null byte injection check
        if "\x00" in path_str:
            raise FileSafetyError("null_byte_injection", path_str)

        # Check exact file denylist
        resolved = Path(path_str).expanduser().resolve()
        for denied in WRITE_DENYLIST_FILES:
            denied_resolved = Path(denied).expanduser().resolve()
            if resolved == denied_resolved:
                raise FileSafetyError("write_denylist", path_str)

        # Check prefix denylist
        for prefix in WRITE_DENYLIST_PREFIXES:
            prefix_resolved = Path(prefix).expanduser().resolve()
            try:
                resolved.relative_to(prefix_resolved)
                raise FileSafetyError("write_denylist_prefix", path_str)
            except ValueError:
                pass

        # Safe root restriction
        if self.safe_root:
            try:
                resolved.relative_to(self.safe_root.resolve())
            except ValueError:
                raise FileSafetyError("outside_safe_root", path_str)

    # ── Read Blocklist ────────────────────────────────────────────────────────

    def check_read(self, path: str | Path) -> None:
        """Check if read from path is allowed. Raises FileSafetyError if not."""
        path_str = self._normalize_path(path)

        # Null byte injection check
        if "\x00" in path_str:
            raise FileSafetyError("null_byte_injection", path_str)

        resolved = Path(path_str).expanduser().resolve()

        # Check exact file blocklist
        for blocked in READ_BLOCKLIST_FILES:
            blocked_resolved = Path(blocked).expanduser().resolve()
            if resolved == blocked_resolved:
                raise FileSafetyError("read_blocklist", path_str)

        # Check prefix blocklist
        for prefix in READ_BLOCKLIST_PREFIXES:
            prefix_resolved = Path(prefix).expanduser().resolve()
            try:
                resolved.relative_to(prefix_resolved)
                raise FileSafetyError("read_blocklist_prefix", path_str)
            except ValueError:
                pass

        # Special: skills/.hub/index-cache
        if "skills/.hub/index-cache" in path_str:
            raise FileSafetyError("prompt_injection_defense", path_str)

    # ── Path Traversal ──────────────────────────────────────────────────────

    def validate_within_dir(self, path: str | Path, root_dir: Path) -> Path:
        """Validate that path is within root_dir.

        Uses resolve() + relative_to(). Raises FileSafetyError if traversal detected.
        """
        path_str = self._normalize_path(path)
        if "\x00" in path_str:
            raise FileSafetyError("null_byte_injection", path_str)

        root_resolved = root_dir.expanduser().resolve()

        # Symlink escape detection - check BEFORE resolving
        path_obj = Path(path_str).expanduser()
        if path_obj.is_symlink():
            link_target = path_obj.readlink()
            try:
                link_target.resolve().relative_to(root_resolved)
            except ValueError:
                raise FileSafetyError("symlink_escape", path_str)

        resolved = path_obj.resolve()

        # Check for traversal
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            raise FileSafetyError("path_traversal", path_str)

        return resolved

    def has_traversal_component(self, path: str | Path) -> bool:
        """Quick check for .. components in path."""
        path_str = str(path)
        parts = Path(path_str).parts
        return ".." in parts

    # ── Read Loop Detection ─────────────────────────────────────────────────

    def check_read_loop(
        self, path: str | Path, offset: int = 0, limit: int = 0
    ) -> tuple[bool, str]:
        """Check for read loops. Returns (should_block, reason).

        Warn at 3 consecutive identical reads, block at 4.
        """
        path_str = str(path)
        current_mtime = 0.0
        try:
            current_mtime = os.path.getmtime(path_str)
        except OSError:
            pass

        state = self._read_loop_state

        # Check if same read parameters
        if (
            state.path == path_str
            and state.offset == offset
            and state.limit == limit
            and state.mtime == current_mtime
        ):
            state.count += 1
        else:
            # Reset state
            state.path = path_str
            state.offset = offset
            state.limit = limit
            state.mtime = current_mtime
            state.count = 1

        if state.count >= 4:
            return True, f"Read loop detected: {path_str} read {state.count} times"
        elif state.count >= 3:
            return False, f"Warning: repeated read of {path_str} ({state.count}x)"

        return False, ""

    # ── Cross-Agent File Locking ────────────────────────────────────────────

    def lock_path(self, path: str | Path) -> int:
        """Acquire advisory lock on a file. Returns fd.

        Uses fcntl on Unix. Caller must call unlock_path(fd) when done.
        """
        path_str = str(path)
        fd = os.open(path_str, os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def unlock_path(self, fd: int) -> None:
        """Release advisory lock."""
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def check_staleness(self, path: str | Path, previous_mtime: float) -> bool:
        """Check if file was modified externally. Returns True if stale."""
        try:
            current_mtime = os.path.getmtime(str(path))
            return current_mtime != previous_mtime
        except OSError:
            return False

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _normalize_path(self, path: str | Path) -> str:
        """Normalize path to string."""
        return str(path).strip()


# ── Convenience Functions ───────────────────────────────────────────────────


def check_write_allowed(path: str | Path, safe_root: Optional[Path] = None) -> None:
    """Convenience function to check write permission."""
    guard = FileSafetyGuard(safe_root=safe_root)
    guard.check_write(path)


def check_read_allowed(path: str | Path) -> None:
    """Convenience function to check read permission."""
    guard = FileSafetyGuard()
    guard.check_read(path)

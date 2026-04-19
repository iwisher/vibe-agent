"""Session logging utility for Vibe Agent."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from vibe.core.config import LogConfig


class SessionLogger:
    """Manages per-session logging with size rotation and aging purge."""

    def __init__(self, config: LogConfig, session_id: str):
        self.config = config
        self.session_id = session_id
        self.log_file: Optional[Path] = None
        self._logger: Optional[logging.Logger] = None

        if self.config.enabled:
            self._setup_logging()
            self._purge_old_logs()

    def _setup_logging(self):
        """Configure logging for the current session."""
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        self.log_file = log_dir / f"session_{self.session_id}.log"
        
        self._logger = logging.getLogger(f"vibe.session.{self.session_id}")
        self._logger.setLevel(logging.DEBUG)
        
        # Avoid propagation to root logger to prevent duplicate terminal output
        self._logger.propagate = False

        # File handler
        handler = logging.FileHandler(self.log_file, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
        self._logger.addHandler(handler)

    def log(self, level: int, message: str):
        """Log a message if logging is enabled."""
        if not self._logger:
            return
        
        # Check file size before logging
        if self.log_file and self.log_file.exists():
            size_mb = self.log_file.stat().st_size / (1024 * 1024)
            if size_mb >= self.config.max_file_size_mb:
                # If file too big, we just stop logging for this session
                # to avoid disk bloat, but we log a final warning.
                if self._logger.isEnabledFor(logging.WARNING):
                    self._logger.warning(f"Log size limit ({self.config.max_file_size_mb}MB) reached. Stopping log.")
                self._logger = None
                return

        self._logger.log(level, message)

    def info(self, message: str):
        self.log(logging.INFO, message)

    def error(self, message: str):
        self.log(logging.ERROR, message)

    def debug(self, message: str):
        self.log(logging.DEBUG, message)

    def _purge_old_logs(self):
        """Remove log files older than retention_days."""
        log_dir = Path(self.config.log_dir)
        if not log_dir.exists():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        
        for log_file in log_dir.glob("session_*.log"):
            try:
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime, tz=timezone.utc)
                if mtime < cutoff:
                    log_file.unlink()
            except Exception:
                # Best effort
                pass


def setup_session_logger(config: LogConfig, session_id: str) -> SessionLogger:
    """Convenience factory."""
    return SessionLogger(config, session_id)

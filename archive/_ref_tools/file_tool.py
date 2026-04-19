"""File tools for reading and writing files."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .tool_system import Tool, ToolResult


class ReadFileTool(Tool):
    """Tool for reading file contents."""

    def __init__(self, max_size: int = 1000000):  # 1MB default
        super().__init__(
            name="read_file",
            description="Read the contents of a file"
        )
        self.max_size = max_size

    def get_schema(self) -> Dict[str, Any]:
        """Return the tool schema."""
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read"
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (optional)",
                    "default": 0
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (optional)",
                    "default": None
                }
            },
            "required": ["path"]
        }

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters before execution."""
        path = params.get("path")
        if not path:
            return False, "Path is required"
        return True, None

    def execute(self, **kwargs) -> ToolResult:
        """Read a file."""
        path = kwargs.get("path")
        offset = kwargs.get("offset", 0)
        limit = kwargs.get("limit")

        try:
            file_path = Path(path).expanduser().resolve()

            # Security check
            if not self._is_path_allowed(file_path):
                return ToolResult(
                    success=False,
                    content=None,
                    error="Access denied: path not allowed"
                )

            # Check if exists
            if not file_path.exists():
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"File not found: {path}"
                )

            # Check if file
            if not file_path.is_file():
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"Not a file: {path}"
                )

            # Check size
            size = file_path.stat().st_size
            if size > self.max_size:
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"File too large ({size} bytes > {self.max_size} bytes)"
                )

            # Read file
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

            # Apply offset and limit
            if offset > 0:
                lines = lines[offset:]
            if limit is not None:
                lines = lines[:limit]

            content = ''.join(lines)

            return ToolResult(
                success=True,
                content=content,
                error=None
            )

        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Error reading file: {str(e)}"
            )

    def _is_path_allowed(self, path: Path) -> bool:
        """Check if a path is allowed for reading."""
        # Block sensitive paths
        sensitive_paths = [
            "/etc/shadow",
            "/etc/sudoers",
            Path.home() / ".ssh" / "id_rsa",
            Path.home() / ".ssh" / "id_ed25519",
        ]

        for sensitive in sensitive_paths:
            if path == Path(sensitive).expanduser().resolve():
                return False

        return True


class WriteFileTool(Tool):
    """Tool for writing file contents."""

    def __init__(self, allow_overwrite: bool = True):
        super().__init__(
            name="write_file",
            description="Write content to a file"
        )
        self.allow_overwrite = allow_overwrite

    def get_schema(self) -> Dict[str, Any]:
        """Return the tool schema."""
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
                "append": {
                    "type": "boolean",
                    "description": "Whether to append instead of overwrite",
                    "default": False
                }
            },
            "required": ["path", "content"]
        }

    def validate_params(self, params: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters before execution."""
        path = params.get("path")
        content = params.get("content")
        if not path:
            return False, "Path is required"
        if content is None:
            return False, "Content is required"
        return True, None

    def execute(self, **kwargs) -> ToolResult:
        """Write to a file."""
        path = kwargs.get("path")
        content = kwargs.get("content")
        append = kwargs.get("append", False)

        try:
            file_path = Path(path).expanduser().resolve()

            # Security check
            if not self._is_path_allowed(file_path):
                return ToolResult(
                    success=False,
                    content=None,
                    error="Access denied: cannot write to this path"
                )

            # Check if exists and overwrite not allowed
            if file_path.exists() and not self.allow_overwrite and not append:
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"File exists and overwrite not allowed: {path}"
                )

            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            mode = 'a' if append else 'w'
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)

            return ToolResult(
                success=True,
                content=f"Successfully wrote to {path}",
                error=None
            )

        except Exception as e:
            return ToolResult(
                success=False,
                content=None,
                error=f"Error writing file: {str(e)}"
            )

    def _is_path_allowed(self, path: Path) -> bool:
        """Check if a path is allowed for writing."""
        # Block sensitive paths
        sensitive_paths = [
            "/etc/passwd",
            "/etc/shadow",
            "/etc/sudoers",
            Path.home() / ".bashrc",
            Path.home() / ".zshrc",
            Path.home() / ".profile",
            Path.home() / ".ssh" / "authorized_keys",
        ]

        for sensitive in sensitive_paths:
            if path == Path(sensitive).expanduser().resolve():
                return False

        return True

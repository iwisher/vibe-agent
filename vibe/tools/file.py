import os

"""File operation tools."""

from pathlib import Path
from typing import Any

from .tool_system import Tool, ToolResult

# Safety limits
_MAX_READ_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_WRITE_SIZE = 5 * 1024 * 1024  # 5 MB


def _redirect_path(path: str) -> str:
    """Redirect /tmp/vibe_* paths to VIBE_EVAL_WORK_DIR if set.
    Preserves relative subdirectories under the work dir.
    Validates that the redirected path stays within work_dir to prevent traversal."""
    work_dir = os.environ.get("VIBE_EVAL_WORK_DIR")
    if not work_dir:
        return path
    if path.startswith("/tmp/"):
        rel = path[len("/tmp/"):]  # e.g., vibe_work/data/file.txt
        redirected = os.path.join(work_dir, rel)
        resolved = os.path.realpath(redirected)
        resolved_work = os.path.realpath(work_dir)
        if not resolved.startswith(resolved_work + os.sep) and resolved != resolved_work:
            # Traversal attempt — keep original path, let it fail normally
            return path
        return redirected
    return path


def _resolve_and_jail(path: str, root_dir: str | None) -> Path:
    """Resolve path and enforce jail if root_dir is set.

    Uses Path.resolve() which follows symlinks and normalizes .. components.
    If any symlink points outside root_dir, the resolved path will be outside
    and the relative_to check will raise PermissionError.
    """
    redirected = _redirect_path(path)
    file_path = Path(redirected).expanduser()

    if root_dir is not None:
        resolved_root = Path(root_dir).expanduser().resolve()
        # resolve() follows symlinks; any escape is caught by relative_to.
        real_path = file_path.resolve()
        try:
            real_path.relative_to(resolved_root)
        except ValueError:
            raise PermissionError(
                f"Path {path} escapes root directory {resolved_root}"
            )
        return real_path

    return file_path.resolve()


class ReadFileTool(Tool):
    """Read contents of a file."""

    def __init__(self, root_dir: str | None = None):
        super().__init__(
            name="read_file",
            description="Read the contents of a file at a given path.",
        )
        self.root_dir = root_dir

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 500},
            },
            "required": ["path"],
        }

    async def execute(self, path: str, offset: int = 1, limit: int = 500, **kwargs) -> ToolResult:
        try:
            file_path = _resolve_and_jail(path, self.root_dir)
            if not file_path.exists():
                return ToolResult(success=False, content=None, error=f"File not found: {path}")
            file_size = file_path.stat().st_size
            if file_size > _MAX_READ_SIZE:
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"File {path} is {file_size} bytes, exceeds max read size of {_MAX_READ_SIZE} bytes.",
                )
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            start = max(0, offset - 1)
            end = start + limit
            selected = lines[start:end]
            content = "".join(selected)
            total = len(lines)
            if total > limit:
                content += f"\n\n[File has {total} lines; showing {start+1}-{min(end, total)}]"
            return ToolResult(success=True, content=content)
        except PermissionError as e:
            return ToolResult(success=False, content=None, error=str(e))
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))


class WriteFileTool(Tool):
    """Write contents to a file."""

    def __init__(self, root_dir: str | None = None):
        super().__init__(
            name="write_file",
            description="Write content to a file. Creates parent directories if needed.",
        )
        self.root_dir = root_dir

    def get_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs) -> ToolResult:
        try:
            content_bytes = content.encode("utf-8")
            if len(content_bytes) > _MAX_WRITE_SIZE:
                return ToolResult(
                    success=False,
                    content=None,
                    error=f"Content is {len(content_bytes)} bytes, exceeds max write size of {_MAX_WRITE_SIZE} bytes.",
                )
            file_path = _resolve_and_jail(path, self.root_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(success=True, content=f"Written: {file_path}")
        except PermissionError as e:
            return ToolResult(success=False, content=None, error=str(e))
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))

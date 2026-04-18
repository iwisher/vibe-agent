import os
"""File operation tools."""

from pathlib import Path
from typing import Any, Dict

from .tool_system import Tool, ToolResult


def _redirect_path(path: str) -> str:
    """Redirect /tmp/vibe_* paths to VIBE_EVAL_WORK_DIR if set.
    Preserves relative subdirectories under the work dir."""
    work_dir = os.environ.get("VIBE_EVAL_WORK_DIR")
    if not work_dir:
        return path
    if path.startswith("/tmp/"):
        rel = path[len("/tmp/"):]  # e.g., vibe_work/data/file.txt
        return os.path.join(work_dir, rel)
    return path


class ReadFileTool(Tool):
    """Read contents of a file."""

    def __init__(self):
        super().__init__(
            name="read_file",
            description="Read the contents of a file at a given path.",
        )

    def get_schema(self) -> Dict[str, Any]:
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
            path = _redirect_path(path)
            file_path = Path(path).expanduser().resolve()
            if not file_path.exists():
                return ToolResult(success=False, content=None, error=f"File not found: {path}")
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
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))


class WriteFileTool(Tool):
    """Write contents to a file."""

    def __init__(self):
        super().__init__(
            name="write_file",
            description="Write content to a file. Creates parent directories if needed.",
        )

    def get_schema(self) -> Dict[str, Any]:
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
            path = _redirect_path(path)
            file_path = Path(path).expanduser().resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(success=True, content=f"Written: {file_path}")
        except Exception as e:
            return ToolResult(success=False, content=None, error=str(e))

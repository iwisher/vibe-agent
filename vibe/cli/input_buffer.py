"""Async input helper with optional prompt_toolkit integration."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from typing import Any

_PROMPT_SESSION: Any = None
_HAS_PT: bool = False

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout

    _HAS_PT = True
except Exception:
    PromptSession = None  # type: ignore[misc,assignment]
    FileHistory = None  # type: ignore[misc,assignment]
    patch_stdout = None  # type: ignore[misc,assignment]


def get_prompt_session(history_path: str | None = None) -> Any:
    """Get or create a PromptSession with optional file history."""
    global _PROMPT_SESSION
    if _PROMPT_SESSION is not None:
        return _PROMPT_SESSION
    if not _HAS_PT:
        return None
    kwargs: dict[str, Any] = {}
    if history_path and FileHistory is not None:
        kwargs["history"] = FileHistory(history_path)
    try:
        _PROMPT_SESSION = PromptSession(**kwargs)
    except Exception:
        return None
    return _PROMPT_SESSION


async def prompt_input(message: str = "", session: Any = None) -> str:
    """Async prompt. Uses prompt_toolkit if available, falls back to input()."""
    if session is not None:
        text = await session.prompt_async(message)
        return text.rstrip("\n")
    return (await asyncio.to_thread(input, message)).rstrip("\n")


def get_patch_stdout():
    """Return patch_stdout context manager if available, else a no-op."""
    if patch_stdout is not None:
        return patch_stdout(raw=True)
    return nullcontext()

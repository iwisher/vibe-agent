from unittest.mock import AsyncMock, patch

from prompt_toolkit.history import FileHistory

import vibe.cli.input_buffer as input_buffer
from vibe.cli.input_buffer import get_patch_stdout, get_prompt_session, prompt_input


def test_get_prompt_session_creates_session_with_history(tmp_path):
    """get_prompt_session must create a PromptSession with FileHistory and enable history search."""
    history_file = tmp_path / "test_history"
    # Reset singleton
    input_buffer._PROMPT_SESSION = None

    session = get_prompt_session(str(history_file))
    assert session is not None
    assert isinstance(session.history, FileHistory)
    assert session.enable_history_search


def test_get_prompt_session_reuses_existing_singleton(tmp_path):
    """Calling get_prompt_session again returns the cached singleton."""
    input_buffer._PROMPT_SESSION = None
    session1 = get_prompt_session(str(tmp_path / "hist"))
    session2 = get_prompt_session(str(tmp_path / "hist"))
    assert session1 is session2


async def test_prompt_input_with_session():
    """prompt_input delegates to session.prompt_async when session is provided."""
    fake_session = AsyncMock()
    fake_session.prompt_async.return_value = "hello world\n"

    result = await prompt_input("❯ ", session=fake_session)
    assert result == "hello world"
    fake_session.prompt_async.assert_awaited_once_with("❯ ")


async def test_prompt_input_fallback_without_session():
    """prompt_input falls back to input() when session is None."""
    with patch("builtins.input", return_value="fallback response\n"):
        result = await prompt_input("❯ ", session=None)
        assert result == "fallback response"


def test_get_patch_stdout():
    """get_patch_stdout returns a usable context manager."""
    ctx = get_patch_stdout()
    assert hasattr(ctx, "__enter__")
    assert hasattr(ctx, "__exit__")

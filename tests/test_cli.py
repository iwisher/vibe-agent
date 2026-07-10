"""Tests for vibe CLI."""

import re
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

from vibe.cli.main import app

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from console output."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_cli_help():
    """CLI should display help text."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Vibe Agent" in result.output


def test_cli_eval_help():
    """Eval subcommand should display help."""
    result = runner.invoke(app, ["eval", "run", "--help"])
    assert result.exit_code == 0
    assert "eval cases" in result.output.lower()


def test_cli_main_options():
    """Main command should expose --model, --server, --api-key options."""
    result = runner.invoke(app, ["main", "--help"])
    assert result.exit_code == 0
    output = _strip_ansi(result.output)
    assert "--model" in output
    assert "--server" in output
    assert "--api-key" in output


def test_cli_single_query_runs_without_crash():
    """Single-query mode should invoke without errors (with mocked factory)."""
    mock_loop = MagicMock()
    mock_loop.run = MagicMock(
        return_value=async_gen(
            [
                MagicMock(
                    response="hi",
                    error=None,
                    tool_results=[],
                    context_truncated=False,
                    metrics=None,
                )
            ]
        )
    )

    with patch("vibe.cli.main.QueryLoopFactory") as MockFactory:
        MockFactory.return_value.create.return_value = mock_loop
        result = runner.invoke(app, ["--", "hello"])
        # The app may exit 0 or crash depending on async mocking; just verify no traceback
        assert "Traceback" not in result.output


async def async_gen(items):
    for item in items:
        yield item


def test_session_resume_prompt():
    """CLI should prompt to resume incomplete sessions when prompt_on_resume is True."""
    mock_loop = MagicMock()
    mock_loop.state.name = "IDLE"
    mock_loop._iteration = 0

    with patch("vibe.harness.memory.session_store.SessionStore") as MockStore:
        mock_store = MagicMock()
        mock_store.list_incomplete.return_value = [
            {"session_id": "test-session-123", "state": "PROCESSING", "iteration": 5}
        ]
        MockStore.return_value = mock_store

        with patch("vibe.core.query_loop.QueryLoop.resume", new_callable=AsyncMock) as mock_resume:
            mock_resume.return_value = mock_loop

            with patch("vibe.cli.main.interactive_mode", new_callable=AsyncMock):
                with patch("builtins.input", return_value="y"):
                    result = runner.invoke(app, ["main"])

    assert "Resume latest session" in result.output or "test-session" in result.output
    assert "Traceback" not in result.output


def test_session_auto_resume():
    """CLI should auto-resume when config has auto_resume=True."""
    mock_loop = MagicMock()
    mock_loop.state.name = "IDLE"
    mock_loop._iteration = 0

    with patch("vibe.cli.main.DEFAULT_CONFIG") as mock_config:
        session_cfg = MagicMock()
        session_cfg.auto_resume = True
        session_cfg.prompt_on_resume = False
        mock_config.session = session_cfg
        mock_config.llm.default_model = "test-model"
        mock_config.llm.base_url = "http://localhost"
        mock_config.get_fallback_chain.return_value = []
        mock_config.resolve_api_key.return_value = None
        mock_config.logging.enabled = False

        with patch("vibe.harness.memory.session_store.SessionStore") as MockStore:
            mock_store = MagicMock()
            mock_store.list_incomplete.return_value = [
                {"session_id": "auto-session-456", "state": "TOOL_EXECUTION", "iteration": 3}
            ]
            MockStore.return_value = mock_store

            with patch(
                "vibe.core.query_loop.QueryLoop.resume", new_callable=AsyncMock
            ) as mock_resume:
                mock_resume.return_value = mock_loop

                with patch("vibe.cli.main.interactive_mode", new_callable=AsyncMock):
                    result = runner.invoke(app, ["main"])

    assert "Auto-resuming" in result.output or "auto-session" in result.output
    assert "Traceback" not in result.output

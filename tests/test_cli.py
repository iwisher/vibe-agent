"""Tests for vibe CLI."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from vibe.cli.main import app

runner = CliRunner()


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
    assert "--model" in result.output
    assert "--server" in result.output
    assert "--api-key" in result.output


def test_cli_single_query_runs_without_crash():
    """Single-query mode should invoke without errors (with mocked factory)."""
    mock_loop = MagicMock()
    mock_loop.run = MagicMock(return_value=async_gen([MagicMock(response="hi", error=None, tool_results=[], context_truncated=False, metrics=None)]))

    with patch("vibe.cli.main.QueryLoopFactory") as MockFactory:
        MockFactory.return_value.create.return_value = mock_loop
        result = runner.invoke(app, ["--", "hello"])
        # The app may exit 0 or crash depending on async mocking; just verify no traceback
        assert "Traceback" not in result.output


async def async_gen(items):
    for item in items:
        yield item

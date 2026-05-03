"""Test CLI skills commands."""
from typer.testing import CliRunner

from vibe.cli.main import app

runner = CliRunner()


def test_skill_list_empty():
    result = runner.invoke(app, ["skill", "list"])
    assert result.exit_code == 0


def test_skill_validate_missing_path():
    result = runner.invoke(app, ["skill", "validate"])
    assert result.exit_code != 0


def test_skill_help():
    result = runner.invoke(app, ["skill", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "install" in result.output
    assert "validate" in result.output
    assert "run" in result.output
    assert "uninstall" in result.output
    assert "create" in result.output

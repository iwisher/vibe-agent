"""Tests for the VibeTUI tiled terminal UI."""

from vibe.cli.tui import VibeTUI, _strip_markup


def test_strip_markup_removes_rich_tags():
    assert _strip_markup("[bold]hello[/bold]") == "hello"
    assert _strip_markup("[dim]thinking[/dim]") == "thinking"
    assert _strip_markup("[red]Error: [/red]fail") == "Error: fail"
    assert _strip_markup("plain text") == "plain text"


def test_vibe_tui_initialization():
    tui = VibeTUI()
    assert tui.thinking_buffer is not None
    assert tui.log_buffer is not None
    assert tui.input_area is not None
    assert tui.layout is not None


def test_vibe_tui_append_thinking_strips_markup():
    tui = VibeTUI()
    tui.append_thinking("[dim]reasoning...[/dim]")
    assert "reasoning..." in tui.thinking_buffer.text
    assert "[dim]" not in tui.thinking_buffer.text


def test_vibe_tui_append_log_strips_markup_and_adds_newline():
    tui = VibeTUI()
    tui.append_log("[green]done[/green]")
    assert tui.log_buffer.text.endswith("\n")
    assert "[green]" not in tui.log_buffer.text


def test_vibe_tui_clear_buffers():
    tui = VibeTUI()
    tui.append_thinking("thought")
    tui.append_log("log")
    tui.clear_thinking()
    tui.clear_log()
    assert tui.thinking_buffer.text == ""
    assert tui.log_buffer.text == ""


def test_vibe_tui_submit_callback():
    tui = VibeTUI()
    received = []
    tui.set_submit_callback(received.append)
    tui._on_submit("hello")
    assert received == ["hello"]
    assert "❯ hello" in tui.log_buffer.text


def test_vibe_tui_create_app():
    tui = VibeTUI()
    app = tui.create_app()
    assert app is not None
    assert app.layout is tui.layout

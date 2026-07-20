"""Tests for the VibeTUI tiled terminal UI."""

from prompt_toolkit.layout import Window
from prompt_toolkit.widgets import TextArea

from vibe.cli.tui import VibeTUI, _strip_markup


def test_strip_markup_removes_rich_tags():
    assert _strip_markup("[bold]hello[/bold]") == "hello"
    assert _strip_markup("[dim]thinking[/dim]") == "thinking"
    assert _strip_markup("[red]Error: [/red]fail") == "Error: fail"
    assert _strip_markup("plain text") == "plain text"


def test_vibe_tui_initialization():
    tui = VibeTUI()
    assert isinstance(tui.thinking_area, TextArea)
    assert isinstance(tui.log_area, TextArea)
    assert isinstance(tui.input_area, TextArea)
    assert tui.thinking_area.read_only
    assert tui.log_area.read_only
    assert tui.layout is not None


def test_vibe_tui_append_thinking_strips_markup():
    tui = VibeTUI()
    tui.append_thinking("[dim]reasoning...[/dim]")
    assert "reasoning..." in tui.thinking_area.text
    assert "[dim]" not in tui.thinking_area.text


def test_vibe_tui_append_log_strips_markup_and_adds_newline():
    tui = VibeTUI()
    tui.append_log("[green]done[/green]")
    assert tui.log_area.text.endswith("\n")
    assert "[green]" not in tui.log_area.text


def test_vibe_tui_clear_buffers():
    tui = VibeTUI()
    tui.append_thinking("thought")
    tui.append_log("log")
    tui.clear_thinking()
    tui.clear_log()
    assert tui.thinking_area.text == ""
    assert tui.log_area.text == ""


def test_vibe_tui_submit_callback():
    tui = VibeTUI()
    received = []
    tui.set_submit_callback(received.append)
    tui._on_submit("hello")
    assert received == ["hello"]
    assert "❯ hello" in tui.log_area.text


def test_vibe_tui_create_app():
    tui = VibeTUI()
    app = tui.create_app()
    assert app is not None
    assert app.layout is tui.layout
    assert app.style is not None


def test_vibe_tui_set_status_updates_input_header():
    tui = VibeTUI()
    tui.set_status("model │ 123 tokens │ 45.6 tok/s")
    header = tui._get_input_header()
    assert "model" in header.value
    assert "123 tokens" in header.value
    assert "45.6 tok/s" in header.value


def test_vibe_tui_has_borders():
    tui = VibeTUI()
    # Should have 2 border windows between 3 tiles
    borders = [
        child
        for child in tui.container.children
        if isinstance(child, Window) and child.style == "class:border"
    ]
    assert len(borders) == 2


def test_vibe_tui_headers_have_styles():
    tui = VibeTUI()
    headers = [
        child
        for child in tui.container.children
        if isinstance(child, Window) and child.style.startswith("class:header.")
    ]
    assert len(headers) == 3
    styles = {h.style for h in headers}
    assert styles == {"class:header.thinking", "class:header.log", "class:header.input"}

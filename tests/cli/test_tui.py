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


# ---------------------------------------------------------------------------
# Layout structure guardrails
# ---------------------------------------------------------------------------


def test_vibe_tui_layout_order():
    """Layout must be: header, content, border, header, content, border, header, input."""
    tui = VibeTUI()
    children = tui.container.children
    assert len(children) == 8

    # Headers
    assert children[0].style == "class:header.thinking"
    assert children[3].style == "class:header.log"
    assert children[6].style == "class:header.input"

    # Content areas (HSplit stores each TextArea's internal window)
    assert children[1].content.buffer is tui.thinking_area.buffer
    assert children[4].content.buffer is tui.log_area.buffer
    assert children[7].content.buffer is tui.input_area.buffer

    # Borders
    assert children[2].style == "class:border"
    assert children[5].style == "class:border"


def test_vibe_tui_input_area_is_focused():
    """Input area must be the focused element in the layout."""
    tui = VibeTUI()
    focused = tui.layout.current_control
    assert focused.buffer is tui.input_area.buffer


def test_vibe_tui_display_areas_are_read_only():
    """Thinking and log areas must be read-only."""
    tui = VibeTUI()
    assert tui.thinking_area.read_only
    assert tui.log_area.read_only
    assert not tui.input_area.read_only


# ---------------------------------------------------------------------------
# Multi-round interaction guardrails
# ---------------------------------------------------------------------------


def test_vibe_tui_layout_stable_after_many_appends():
    """After many rounds of content, headers and borders must stay in place."""
    tui = VibeTUI()
    # Simulate 50 rounds of thinking + log output
    for i in range(50):
        tui.append_thinking(f"thinking round {i}... ")
        tui.append_log(f"log round {i}")

    children = tui.container.children
    assert len(children) == 8
    assert children[0].style == "class:header.thinking"
    assert children[3].style == "class:header.log"
    assert children[6].style == "class:header.input"
    assert children[2].style == "class:border"
    assert children[5].style == "class:border"


def test_vibe_tui_content_accumulates_in_order():
    """Content must accumulate in the order appended."""
    tui = VibeTUI()
    for i in range(5):
        tui.append_log(f"line {i}")

    text = tui.log_area.text
    lines = [line for line in text.split("\n") if line]
    assert lines == [f"line {i}" for i in range(5)]


def test_vibe_tui_thinking_and_log_are_independent():
    """Thinking and log buffers must not interfere."""
    tui = VibeTUI()
    tui.append_thinking("thought")
    tui.append_log("log entry")

    assert "thought" in tui.thinking_area.text
    assert "thought" not in tui.log_area.text
    assert "log entry" in tui.log_area.text
    assert "log entry" not in tui.thinking_area.text


# ---------------------------------------------------------------------------
# Scrolling and cursor behavior
# ---------------------------------------------------------------------------


def test_vibe_tui_cursor_stays_at_bottom_after_append():
    """Cursor must be at end of buffer after append so new content is visible."""
    tui = VibeTUI()
    tui.append_log("first")
    assert tui.log_area.buffer.cursor_position == len(tui.log_area.text)

    tui.append_log("second")
    assert tui.log_area.buffer.cursor_position == len(tui.log_area.text)


def test_vibe_tui_clear_resets_cursor_position():
    """Clearing must reset cursor position to 0."""
    tui = VibeTUI()
    tui.append_log("content")
    tui.clear_log()
    assert tui.log_area.buffer.cursor_position == 0


# ---------------------------------------------------------------------------
# Style and appearance
# ---------------------------------------------------------------------------


def test_vibe_tui_style_has_header_and_border_classes():
    """Application style must define header and border classes."""
    tui = VibeTUI()
    app = tui.create_app()
    style = app.style

    # Check that style rules exist for our classes
    style_dict = {rule[0]: rule[1] for rule in style.style_rules}
    assert "header.thinking" in style_dict
    assert "header.log" in style_dict
    assert "header.input" in style_dict
    assert "border" in style_dict


def test_vibe_tui_input_header_contains_status_placeholder():
    """Input header must include the status text placeholder."""
    tui = VibeTUI()
    header = tui._get_input_header()
    assert "Input" in header.value
    assert "│" in header.value


# ---------------------------------------------------------------------------
# Queue info in input tile header
# ---------------------------------------------------------------------------


def test_vibe_tui_set_queue_info_empty_queue():
    """Empty queue must not show queue info."""
    tui = VibeTUI()
    tui.set_queue_info(0)
    header = tui._get_input_header()
    assert "queue:" not in header.value


def test_vibe_tui_set_queue_info_with_pending():
    """Pending messages must show count and preview."""
    tui = VibeTUI()
    tui.set_queue_info(2, "what is the weather today?")
    header = tui._get_input_header()
    assert "queue:2" in header.value
    assert "what is the weather" in header.value


def test_vibe_tui_set_queue_info_without_preview():
    """Pending messages without preview show only count."""
    tui = VibeTUI()
    tui.set_queue_info(3)
    header = tui._get_input_header()
    assert "queue:3" in header.value


def test_vibe_tui_set_queue_info_clears_when_empty():
    """Queue info must disappear when queue becomes empty."""
    tui = VibeTUI()
    tui.set_queue_info(2, "test message")
    assert "queue:2" in tui._get_input_header().value
    tui.set_queue_info(0)
    assert "queue:" not in tui._get_input_header().value


def test_vibe_tui_queue_info_coexists_with_status():
    """Queue info must appear alongside model/token status."""
    tui = VibeTUI()
    tui.set_status("model │ 100 tokens │ 50 tok/s")
    tui.set_queue_info(1, "next msg")
    header = tui._get_input_header()
    assert "model" in header.value
    assert "100 tokens" in header.value
    assert "queue:1" in header.value


# ---------------------------------------------------------------------------
# Regression: critique findings
# ---------------------------------------------------------------------------


def test_regression_c2_input_header_escapes_html():
    """Hostile queue preview content must not crash the HTML header parser."""
    tui = VibeTUI()
    tui.set_queue_info(1, "a < b & use <div> tag")
    header = tui._get_input_header()  # must not raise
    assert "a &lt; b" in header.value
    assert "&amp;" in header.value

    tui.set_status("plain & simple")
    header = tui._get_input_header()  # must not raise
    assert "&amp;" in header.value


def test_regression_c3_append_log_chunk_has_no_newline():
    """Stream chunks must append without per-chunk newlines."""
    tui = VibeTUI()
    for chunk in ["Hello", ", ", "world", "!"]:
        tui.append_log_chunk(chunk)
    assert tui.log_area.text == "Hello, world!"


def test_regression_h1_buffer_is_capped():
    """Buffers must not grow unbounded."""
    from vibe.cli.tui import _MAX_BUFFER_CHARS

    tui = VibeTUI()
    for i in range(2000):
        tui.append_log(f"line {i} " + "x" * 80)
    assert len(tui.log_area.text) <= _MAX_BUFFER_CHARS
    # Most recent content is preserved
    assert "line 1999" in tui.log_area.text


def test_regression_h2_status_invalidate_only_on_change():
    """set_status/set_queue_info must not invalidate when value is unchanged."""
    tui = VibeTUI()
    calls = []
    tui._invalidate = lambda: calls.append(1)

    tui.set_status("a")
    assert len(calls) == 1
    tui.set_status("a")  # unchanged — no redraw
    assert len(calls) == 1
    tui.set_status("b")
    assert len(calls) == 2

    tui.set_queue_info(1, "msg")
    assert len(calls) == 3
    tui.set_queue_info(1, "msg")  # unchanged
    assert len(calls) == 3
    tui.set_queue_info(0)  # changed
    assert len(calls) == 4


def test_regression_queue_preview_ellipsis_only_for_long_messages():
    """Short previews must not get a trailing ellipsis."""
    tui = VibeTUI()
    tui.set_queue_info(1, "short")
    assert "..." not in tui._get_input_header().value

    tui.set_queue_info(1, "x" * 60)
    assert "..." in tui._get_input_header().value


def test_regression_exit_method_calls_app_exit():
    """Public exit() must delegate to the application."""

    class FakeApp:
        exited = False

        def exit(self):
            self.exited = True

    tui = VibeTUI()
    fake = FakeApp()
    tui._app = fake
    tui.exit()
    assert fake.exited


def test_regression_strip_markup_does_not_swallow_across_newlines():
    """Unmatched '[' must not eat subsequent lines."""
    text = "first [unclosed tag\nsecond line"
    result = _strip_markup(text)
    assert "second line" in result


def test_vibe_tui_history_path_wires_file_history(tmp_path):
    """Passing history_path must give the input area a FileHistory."""
    from prompt_toolkit.history import FileHistory

    tui = VibeTUI(history_path=str(tmp_path / "history"))
    assert isinstance(tui.input_area.buffer.history, FileHistory)


def test_vibe_tui_enter_appends_to_history():
    """Submitting input must append the text to history."""
    submitted = []
    tui = VibeTUI()
    tui.set_submit_callback(submitted.append)

    # Find the enter key binding
    kb = tui.container.key_bindings
    bindings = [b for b in kb.bindings if any(k.value in ("c-m", "enter") for k in b.keys)]
    assert bindings, "Enter key binding must exist"
    enter_binding = bindings[0]

    # Type first command and trigger enter
    tui.input_area.text = "first command"
    enter_binding.handler(None)
    assert submitted == ["first command"]
    assert tui.input_area.text == ""

    # Type second command and trigger enter
    tui.input_area.text = "second command"
    enter_binding.handler(None)
    assert submitted == ["first command", "second command"]
    assert tui.input_area.text == ""

    # Verify history recorded the strings
    history_strings = list(tui.input_area.buffer.history.get_strings())
    assert "first command" in history_strings
    assert "second command" in history_strings


def test_vibe_tui_history_navigation_shortcuts():
    """Up and c-p must navigate back; Down and c-n must navigate forward in history."""
    tui = VibeTUI()
    tui.set_submit_callback(lambda text: None)

    kb = tui.container.key_bindings
    up_binding = [b for b in kb.bindings if any(k.value in ("up", "c-p") for k in b.keys)][0]
    down_binding = [b for b in kb.bindings if any(k.value in ("down", "c-n") for k in b.keys)][0]
    enter_binding = [b for b in kb.bindings if any(k.value in ("c-m", "enter") for k in b.keys)][0]

    # Populate history
    tui.input_area.text = "echo alpha"
    enter_binding.handler(None)
    tui.input_area.text = "echo beta"
    enter_binding.handler(None)

    # Initially empty
    assert tui.input_area.text == ""

    # Press Up -> should recall "echo beta"
    up_binding.handler(None)
    assert tui.input_area.text == "echo beta"

    # Press Up again -> should recall "echo alpha"
    up_binding.handler(None)
    assert tui.input_area.text == "echo alpha"

    # Press Down -> should go forward to "echo beta"
    down_binding.handler(None)
    assert tui.input_area.text == "echo beta"

    # Press Down again -> should return to empty
    down_binding.handler(None)
    assert tui.input_area.text == ""

    # Press Down again -> should return to empty
    down_binding.handler(None)
    assert tui.input_area.text == ""

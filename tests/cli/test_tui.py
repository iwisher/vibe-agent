"""Tests for the VibeTUI tiled terminal UI."""

from prompt_toolkit.document import Document
from prompt_toolkit.layout import Window
from prompt_toolkit.widgets import TextArea

from vibe.cli.tui import TUIKeywordLexer, VibeTUI, _strip_markup


def test_strip_markup_removes_rich_tags():
    assert _strip_markup("[bold]hello[/bold]") == "hello"
    assert _strip_markup("[dim]thinking[/dim]") == "thinking"
    assert _strip_markup("[red]Error: [/red]fail") == "Error: fail"
    assert _strip_markup("plain text") == "plain text"


def test_tui_keyword_lexer_tokenization():
    lexer = TUIKeywordLexer()
    line_0 = "❯ 💻 [TOOL:bash] python scripts/fetch.py --url https://api.com in 124ms ✨ ✔ SUCCESS"
    line_1 = "💥 ✖ Error: file not found"
    doc = Document(f"{line_0}\n{line_1}")
    getter = lexer.lex_document(doc)

    line0_tokens = getter(0)
    # Tokens should preserve all original text exactly
    reconstructed0 = "".join(text for _, text in line0_tokens)
    assert reconstructed0 == line_0

    # Verify style classes attached
    styles0 = [style for style, _ in line0_tokens]
    assert "class:token.prompt" in styles0
    assert "class:token.tool" in styles0
    assert "class:token.url" in styles0
    assert "class:token.metric" in styles0
    assert "class:token.success" in styles0

    line1_tokens = getter(1)
    reconstructed1 = "".join(text for _, text in line1_tokens)
    assert reconstructed1 == "💥 ✖ Error: file not found"
    styles1 = [style for style, _ in line1_tokens]
    assert "class:token.error" in styles1


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


def test_vibe_tui_top_header_state_and_metrics():
    tui = VibeTUI()
    tui.set_model("qwen3:8b")
    tui.set_system_state("BUSY")
    tui.set_metrics(1200, 48.5, cost=0.0024)

    top_header = tui._get_top_header()
    assert "VIBE AGENT" in top_header.value
    assert "qwen3:8b" in top_header.value
    assert "BUSY" in top_header.value
    assert "1200 tokens" in top_header.value
    assert "48.5 tok/s" in top_header.value
    assert "$0.0024" in top_header.value


def test_vibe_tui_has_labeled_dividers():
    tui = VibeTUI()
    # Two labeled section dividers: before the log tile and before the input tile
    dividers = [
        child
        for child in tui.container.children
        if isinstance(child, Window) and child.style.startswith("class:border")
    ]
    assert len(dividers) == 2
    styles = {d.style for d in dividers}
    assert styles == {"class:border.log", "class:border.input"}
    # The log divider is static and carries its section label
    log_divider = next(d for d in dividers if d.style == "class:border.log")
    assert "WORKING LOG" in log_divider.content.text


def test_vibe_tui_headers_have_styles():
    tui = VibeTUI()
    headers = [
        child
        for child in tui.container.children
        if isinstance(child, Window) and child.style.startswith("class:header.")
    ]
    assert len(headers) == 3
    styles = {h.style for h in headers}
    assert styles == {
        "class:header.system",
        "class:header.thinking",
        "class:header.shortcuts",
    }


# ---------------------------------------------------------------------------
# Layout structure guardrails
# ---------------------------------------------------------------------------


def test_vibe_tui_layout_order():
    """Layout must be: top_bar, thinking_header, thinking, log_divider, log,
    input_divider, input, shortcuts."""
    tui = VibeTUI()
    children = tui.container.children
    assert len(children) == 8

    # Headers & Bars
    assert children[0].style == "class:header.system"
    assert children[1].style == "class:header.thinking"
    assert children[7].style == "class:header.shortcuts"

    # Content areas (HSplit stores each TextArea's internal window)
    assert children[2].content.buffer is tui.thinking_area.buffer
    assert children[4].content.buffer is tui.log_area.buffer
    assert children[6].content.buffer is tui.input_area.buffer

    # Labeled section dividers
    assert children[3].style == "class:border.log"
    assert children[5].style == "class:border.input"


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
    """After many rounds of content, headers and dividers must stay in place."""
    tui = VibeTUI()
    # Simulate 50 rounds of thinking + log output
    for i in range(50):
        tui.append_thinking(f"thinking round {i}... ")
        tui.append_log(f"log round {i}")

    children = tui.container.children
    assert len(children) == 8
    assert children[0].style == "class:header.system"
    assert children[1].style == "class:header.thinking"
    assert children[3].style == "class:border.log"
    assert children[5].style == "class:border.input"
    assert children[7].style == "class:header.shortcuts"


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
    assert "USER PROMPT" in header.value
    assert "ready" in header.value
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


# ---------------------------------------------------------------------------
# Expandable input area
# ---------------------------------------------------------------------------


def _find_binding(tui, *key_values):
    aliases = {
        "tab": ("tab", "c-i"),
        "s-tab": ("s-tab", "c-y", "backtab"),
        "enter": ("enter", "c-m"),
    }
    kb = tui.container.key_bindings
    for b in kb.bindings:
        keys = tuple(k.value for k in b.keys)
        if len(keys) != len(key_values):
            continue
        matched = True
        for k_val, req in zip(keys, key_values):
            req_set = aliases.get(req, (req,))
            if k_val != req and k_val not in req_set:
                matched = False
                break
        if matched:
            return b
    return None


def test_vibe_tui_input_area_supports_multiline_wrapping():
    """The input area must be multiline + wrapped so it can grow vertically."""
    tui = VibeTUI()
    assert tui.input_area.buffer.multiline()
    assert tui.input_area.wrap_lines


def test_vibe_tui_input_height_collapsed_by_default():
    tui = VibeTUI()
    assert tui._input_expanded is False
    assert tui._input_height() == 1


def test_vibe_tui_ctrl_t_toggles_input_expansion():
    tui = VibeTUI()
    toggle = _find_binding(tui, "c-t")
    assert toggle is not None, "Ctrl-T toggle binding must exist"

    toggle.handler(None)
    assert tui._input_expanded is True
    # No running app -> fallback 24 rows -> half = 12
    assert tui._input_height() == 12

    toggle.handler(None)
    assert tui._input_expanded is False
    assert tui._input_height() == 1


def test_vibe_tui_expanded_input_never_exceeds_half_screen():
    """Expanded height must be capped at 50% of the terminal height."""

    class FakeOutput:
        def get_size(self):
            return SimpleSize(rows=40)

    class SimpleSize:
        def __init__(self, rows):
            self.rows = rows

    class FakeApp:
        output = FakeOutput()

        def invalidate(self):
            pass

    tui = VibeTUI()
    tui._app = FakeApp()
    tui._toggle_input_expanded()
    assert tui._input_height() == 20  # exactly 50% of 40 rows


async def test_vibe_tui_alt_enter_inserts_newline_only_when_expanded():
    tui = VibeTUI()
    alt_enter = _find_binding(tui, "escape", "c-m")
    assert alt_enter is not None, "Alt-Enter newline binding must exist"

    # Collapsed: no-op (Enter submits; single-line input)
    tui.input_area.text = "line one"
    alt_enter.handler(None)
    assert tui.input_area.text == "line one"

    # Expanded: inserts a newline at the cursor
    tui.input_area.buffer.cursor_position = len(tui.input_area.text)
    tui._toggle_input_expanded()
    alt_enter.handler(None)
    assert tui.input_area.text == "line one\n"


def test_vibe_tui_input_divider_shows_expand_hint():
    tui = VibeTUI()
    assert "[Ctrl-T] Expand" in tui._get_input_header().value
    tui._toggle_input_expanded()
    assert "[Ctrl-T] Collapse" in tui._get_input_header().value


def test_vibe_tui_footer_mentions_expand_shortcut():
    tui = VibeTUI()
    footer = tui.container.children[-1]
    assert "Ctrl-T" in footer.content.text
    assert "PgUp/PgDn" in footer.content.text


# ---------------------------------------------------------------------------
# Section scrolling and focus navigation (PageUp / PageDown / Tab)
# ---------------------------------------------------------------------------


def test_vibe_tui_scroll_log_up_down():
    tui = VibeTUI()
    for i in range(30):
        tui.append_log(f"log line {i}")
    bottom_pos = tui.log_area.buffer.cursor_position
    assert bottom_pos > 0

    # Scroll up moves cursor position backwards
    tui.scroll_log_up(count=10)
    assert tui.log_area.buffer.cursor_position < bottom_pos
    scrolled_pos = tui.log_area.buffer.cursor_position

    # Scroll down moves cursor position forwards
    tui.scroll_log_down(count=5)
    assert tui.log_area.buffer.cursor_position > scrolled_pos


def test_vibe_tui_scroll_thinking_up_down():
    tui = VibeTUI()
    for i in range(30):
        tui.append_thinking(f"thinking trace {i}\n")
    bottom_pos = tui.thinking_area.buffer.cursor_position
    assert bottom_pos > 0

    # Scroll up moves cursor position backwards
    tui.scroll_thinking_up(count=10)
    assert tui.thinking_area.buffer.cursor_position < bottom_pos
    scrolled_pos = tui.thinking_area.buffer.cursor_position

    # Scroll down moves cursor position forwards
    tui.scroll_thinking_down(count=5)
    assert tui.thinking_area.buffer.cursor_position > scrolled_pos


def test_vibe_tui_pageup_pagedown_keybindings():
    tui = VibeTUI()
    for i in range(30):
        tui.append_log(f"log entry {i}")

    pageup = _find_binding(tui, "pageup")
    pagedown = _find_binding(tui, "pagedown")
    assert pageup is not None
    assert pagedown is not None

    bottom_pos = tui.log_area.buffer.cursor_position
    pageup.handler(None)
    assert tui.log_area.buffer.cursor_position < bottom_pos

    scrolled_pos = tui.log_area.buffer.cursor_position
    pagedown.handler(None)
    assert tui.log_area.buffer.cursor_position > scrolled_pos


def test_vibe_tui_thinking_scroll_keybindings():
    tui = VibeTUI()
    for i in range(30):
        tui.append_thinking(f"thought {i}\n")

    alt_pageup = _find_binding(tui, "escape", "pageup")
    assert alt_pageup is not None

    bottom_pos = tui.thinking_area.buffer.cursor_position
    alt_pageup.handler(None)
    assert tui.thinking_area.buffer.cursor_position < bottom_pos


def test_vibe_tui_tab_focus_cycle_and_escape():
    tui = VibeTUI()
    tab = _find_binding(tui, "tab")
    s_tab = _find_binding(tui, "s-tab")
    escape = _find_binding(tui, "escape")
    assert tab is not None
    assert s_tab is not None
    assert escape is not None

    # Initial focus is input_area
    assert tui.layout.has_focus(tui.input_area)

    # Tab: input -> log
    tab.handler(None)
    assert tui.layout.has_focus(tui.log_area)

    # Tab: log -> thinking
    tab.handler(None)
    assert tui.layout.has_focus(tui.thinking_area)

    # Tab: thinking -> input
    tab.handler(None)
    assert tui.layout.has_focus(tui.input_area)

    # Shift-Tab: input -> thinking
    s_tab.handler(None)
    assert tui.layout.has_focus(tui.thinking_area)

    # Escape: returns immediately to input
    escape.handler(None)
    assert tui.layout.has_focus(tui.input_area)


# ---------------------------------------------------------------------------
# Mouse interaction and scrolling tests
# ---------------------------------------------------------------------------


def test_vibe_tui_mouse_support_enabled_in_app():
    """Application must have mouse support enabled."""
    tui = VibeTUI()
    app = tui.create_app()
    assert app.mouse_support() is True or bool(app.mouse_support)


def test_vibe_tui_mouse_scroll_thinking_area():
    """Mouse wheel scrolling on thinking area moves cursor position up/down."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    tui = VibeTUI()
    for i in range(30):
        tui.append_thinking(f"thinking trace {i}\n")
    bottom_pos = tui.thinking_area.buffer.cursor_position
    assert bottom_pos > 0

    scroll_up = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )
    scroll_down = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_DOWN,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )

    tui.thinking_area.control.mouse_handler(scroll_up)
    assert tui.thinking_area.buffer.cursor_position < bottom_pos
    scrolled_pos = tui.thinking_area.buffer.cursor_position

    tui.thinking_area.control.mouse_handler(scroll_down)
    assert tui.thinking_area.buffer.cursor_position > scrolled_pos


def test_vibe_tui_mouse_scroll_log_area():
    """Mouse wheel scrolling on log area moves cursor position up/down."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    tui = VibeTUI()
    for i in range(30):
        tui.append_log(f"log entry {i}")
    bottom_pos = tui.log_area.buffer.cursor_position
    assert bottom_pos > 0

    scroll_up = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )
    scroll_down = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_DOWN,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )

    tui.log_area.control.mouse_handler(scroll_up)
    assert tui.log_area.buffer.cursor_position < bottom_pos
    scrolled_pos = tui.log_area.buffer.cursor_position

    tui.log_area.control.mouse_handler(scroll_down)
    assert tui.log_area.buffer.cursor_position > scrolled_pos


def test_vibe_tui_mouse_scroll_and_click_headers():
    """Mouse wheel scrolling on section headers/dividers scrolls areas, and click focuses them."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    tui = VibeTUI()
    for i in range(30):
        tui.append_thinking(f"thinking trace {i}\n")
        tui.append_log(f"log entry {i}")

    scroll_up = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )
    click = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.MOUSE_UP,
        button=MouseButton.LEFT,
        modifiers=frozenset(),
    )

    # Thinking header: child[1]
    thinking_header_ctrl = tui.container.children[1].content
    thinking_bottom = tui.thinking_area.buffer.cursor_position
    thinking_header_ctrl.mouse_handler(scroll_up)
    assert tui.thinking_area.buffer.cursor_position < thinking_bottom
    thinking_header_ctrl.mouse_handler(click)
    assert tui.layout.has_focus(tui.thinking_area)

    # Log divider: child[3]
    log_divider_ctrl = tui.container.children[3].content
    log_bottom = tui.log_area.buffer.cursor_position
    log_divider_ctrl.mouse_handler(scroll_up)
    assert tui.log_area.buffer.cursor_position < log_bottom
    log_divider_ctrl.mouse_handler(click)
    assert tui.layout.has_focus(tui.log_area)

    # Input divider: child[5]
    input_divider_ctrl = tui.container.children[5].content
    input_divider_ctrl.mouse_handler(click)
    assert tui.layout.has_focus(tui.input_area)

    # Top header: child[0]
    top_header_ctrl = tui.container.children[0].content
    top_header_ctrl.mouse_handler(click)
    assert tui.layout.has_focus(tui.input_area)


def test_vibe_tui_mouse_scroll_input_history():
    """Mouse wheel scrolling on single-line input scrolls history backward/forward."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    tui = VibeTUI()
    tui.set_submit_callback(lambda text: None)
    tui.input_area.buffer.history.append_string("cmd alpha")
    tui.input_area.buffer.history.append_string("cmd beta")

    scroll_up = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_UP,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )
    scroll_down = MouseEvent(
        position=Point(0, 0),
        event_type=MouseEventType.SCROLL_DOWN,
        button=MouseButton.NONE,
        modifiers=frozenset(),
    )

    tui.input_area.control.mouse_handler(scroll_up)
    assert tui.input_area.text == "cmd beta"

    tui.input_area.control.mouse_handler(scroll_up)
    assert tui.input_area.text == "cmd alpha"

    tui.input_area.control.mouse_handler(scroll_down)
    assert tui.input_area.text == "cmd beta"

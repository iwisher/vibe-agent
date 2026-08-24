"""Tests for the shared CLI rendering helpers in vibe/cli/rendering.py."""

import io

from rich.console import Console

from vibe.cli.rendering import (
    MAX_TOOL_OUTPUT_CHARS,
    format_metrics_line,
    format_shortcuts_help,
    format_tool_result_text,
    get_session_cost,
    populate_console_from_messages,
    populate_tui_from_messages,
    render_error,
    render_response,
    render_tool_result_from_metadata,
    safe_print_chunk,
    stringify_content,
    truncate_output,
)
from vibe.core.query_loop import Metrics
from vibe.tools.tool_system import ToolResult


def _make_console() -> Console:
    """Console writing to an in-memory buffer with ANSI styles enabled."""
    return Console(file=io.StringIO(), force_terminal=True, width=100)


def _output(console: Console) -> str:
    return console.file.getvalue()


def test_render_response_renders_markdown():
    console = _make_console()
    render_response(console, "# Title\n\nSome **bold** text.")
    out = _output(console)
    assert "Title" in out
    assert "bold" in out
    # Markdown markers must be consumed by the renderer, not shown raw.
    assert "**bold**" not in out


def test_render_response_empty_text_is_noop():
    console = _make_console()
    render_response(console, "")
    assert _output(console) == ""


def test_truncate_output_short_text_unchanged():
    assert truncate_output("short") == "short"
    assert truncate_output("x" * MAX_TOOL_OUTPUT_CHARS) == "x" * MAX_TOOL_OUTPUT_CHARS


def test_truncate_output_reports_exact_omitted_count():
    text = "y" * 5000
    result = truncate_output(text)
    assert result.startswith("y" * MAX_TOOL_OUTPUT_CHARS)
    expected_omitted = 5000 - MAX_TOOL_OUTPUT_CHARS
    assert f"… truncated ({expected_omitted} more chars)" in result


def test_truncate_output_custom_limit():
    result = truncate_output("abcdefghij", max_chars=4)
    assert result == "abcd\n… truncated (6 more chars)"


def test_render_tool_result_shows_name_args_and_duration_from_metadata():
    console = _make_console()
    tr = ToolResult(
        success=True,
        content="file listing here",
        metadata={
            "tool_name": "bash",
            "tool_args": {"command": "ls -la"},
            "duration_s": 0.3,
        },
    )
    render_tool_result_from_metadata(console, tr)
    out = _output(console)
    assert "bash" in out
    assert "command=ls -la" in out
    assert "0.3s" in out
    assert "file listing here" in out


def test_render_tool_result_falls_back_to_generic_titles():
    console = _make_console()
    render_tool_result_from_metadata(console, ToolResult(success=True, content="ok"))
    assert "Tool Result" in _output(console)

    console = _make_console()
    render_tool_result_from_metadata(console, ToolResult(success=False, content=None, error="boom"))
    out = _output(console)
    assert "Tool Error" in out
    assert "boom" in out


def test_render_tool_result_truncates_long_output():
    console = _make_console()
    long_content = "z" * (MAX_TOOL_OUTPUT_CHARS + 500)
    render_tool_result_from_metadata(console, ToolResult(success=True, content=long_content))
    out = _output(console)
    assert "truncated (500 more chars)" in out
    # The full untruncated body must not appear.
    assert "z" * (MAX_TOOL_OUTPUT_CHARS + 1) not in out


def test_render_tool_result_json_body_is_highlighted():
    console = _make_console()
    render_tool_result_from_metadata(
        console, ToolResult(success=True, content={"answer": 42, "ok": True})
    )
    out = _output(console)
    assert '"answer"' in out
    assert "42" in out


def test_render_error_preserves_hint_and_model():
    console = _make_console()
    render_error(
        console,
        message="connection refused",
        hint="check that the server is running",
        model="qwen3:8b",
    )
    out = _output(console)
    assert "connection refused" in out
    assert "check that the server is running" in out
    assert "qwen3:8b" in out
    assert "Error" in out


def test_render_error_escapes_markup_in_message():
    console = _make_console()
    render_error(console, message="bad tag [red]oops[/red] here")
    out = _output(console)
    assert "[red]oops[/red]" in out


def test_safe_print_chunk_does_not_interpret_markup():
    console = _make_console()
    safe_print_chunk(console, "use [brackets] and [bold] literally")
    out = _output(console)
    assert "[brackets]" in out
    assert "[bold]" in out


def test_safe_print_chunk_no_trailing_newline():
    console = _make_console()
    safe_print_chunk(console, "Hello")
    safe_print_chunk(console, " world")
    assert _output(console) == "Hello world"


def test_safe_print_chunk_empty_is_noop():
    console = _make_console()
    safe_print_chunk(console, "")
    assert _output(console) == ""


def test_format_tool_result_text_includes_name_and_truncates():
    tr = ToolResult(
        success=True,
        content="x" * 5000,
        metadata={"tool_name": "read_file"},
    )
    text = format_tool_result_text(tr, max_chars=100)
    assert text.startswith("✨ ✔ [TOOL:read_file]: " + "x" * 100)
    assert "truncated (4900 more chars)" in text


def test_format_tool_result_text_error_variant():
    tr = ToolResult(success=False, content=None, error="nope")
    text = format_tool_result_text(tr)
    assert text.startswith("💥 ✖ [TOOL:tool]: nope")


def test_stringify_content_handles_dicts_and_none():
    assert stringify_content(None) == ""
    assert stringify_content("plain") == "plain"
    assert '"a": 1' in stringify_content({"a": 1})


def test_format_metrics_line_basic_and_extras():
    m = Metrics(
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        elapsed_seconds=1.0,
        tokens_per_second=30.0,
        reasoning_tokens=5,
    )
    line = format_metrics_line(m)
    assert line == "30 tokens (5 reasoning) | 1.0s | 30.0 tok/s"

    line = format_metrics_line(m, model_used="qwen3:8b", current_model="gpt-4", session_cost=0.0123)
    assert "$0.0123" in line
    assert "(via fallback model: qwen3:8b)" in line

    # Same model as the default -> no fallback note; zero cost -> no cost.
    line = format_metrics_line(m, model_used="gpt-4", current_model="gpt-4", session_cost=0.0)
    assert "fallback" not in line
    assert "$" not in line


def test_get_session_cost_returns_none_without_tracker():
    class Bare:
        pass

    assert get_session_cost(Bare()) is None

    class WithRouter:
        cost_router = type("R", (), {"spend_tracker": None})()

    assert get_session_cost(WithRouter()) is None


def test_get_session_cost_reads_spend_tracker():
    class FakeTracker:
        def get_spend(self, session_id):
            return {"total_cost": 0.5}

    class FakeRouter:
        spend_tracker = FakeTracker()
        session_id = "sess"

    class FakeLoop:
        cost_router = FakeRouter()

    assert get_session_cost(FakeLoop()) == 0.5


def test_format_shortcuts_help():
    help_text = format_shortcuts_help()
    assert "PageUp / PageDown" in help_text
    assert "Alt-PageUp / Alt-PageDown" in help_text
    assert "Ctrl-T" in help_text
    assert "/shortcuts" in help_text
    assert "/clear" in help_text


def test_populate_tui_from_messages():
    from vibe.cli.tui import VibeTUI
    from vibe.core.query_loop import Message

    tui = VibeTUI()
    messages = [
        Message(role="system", content="System instruction"),
        Message(role="user", content="List all files"),
        Message(
            role="assistant",
            content="<think>Thinking about running ls</think>Running the command now.",
            tool_calls=[
                {
                    "id": "call_1",
                    "function": {"name": "bash", "arguments": '{"command": "ls -la"}'},
                }
            ],
        ),
        Message(
            role="tool",
            content="file1.txt\nfile2.txt",
            tool_call_id="call_1",
            metadata={"tool_name": "bash"},
        ),
        Message(role="assistant", content="Found 2 files: file1.txt, file2.txt"),
    ]

    populate_tui_from_messages(tui, messages)

    # Verify thinking area received thinking content
    assert "Thinking about running ls" in tui.thinking_area.text

    # Verify log area received user prompt, tool call, tool result, and assistant answers
    log_text = tui.log_area.text
    assert "❯ List all files" in log_text
    assert "💬 Running the command now." in log_text
    assert "[TOOL:bash] command=ls -la" in log_text
    assert "✨ ✔ [TOOL:bash] file1.txt" in log_text
    assert "💬 Found 2 files: file1.txt, file2.txt" in log_text


def test_populate_console_from_messages():
    from vibe.core.query_loop import Message

    console = _make_console()
    messages = [
        Message(role="system", content="System instruction"),
        Message(role="user", content="What is 2+2?"),
        Message(
            role="assistant",
            content="<think>Simple arithmetic</think>The answer is **4**.",
        ),
    ]

    populate_console_from_messages(console, messages)
    out = _output(console)
    assert "What is 2+2?" in out
    assert "The answer is" in out
    assert "4" in out

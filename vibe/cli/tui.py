"""Tiled terminal UI for Vibe Agent interactive mode.

Provides a full-screen prompt_toolkit layout with:
- Top system bar: system title, model badge, live state, token stats
- Top tile: agent thinking / reasoning stream with TrueColor syntax highlighting
- Middle tile: working log / tool results / metrics with real-time keyword lexer
- Labeled unicode dividers (╞══ … ══╡) marking each section boundary
- Bottom tile: user prompt box, expandable to half the screen via Ctrl-T
- Bottom action bar: keyboard shortcuts and command helpers
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

# Match Rich-style tags but never across newlines, so an unmatched "[" does not
# swallow the rest of the buffer.
_RICH_MARKUP = re.compile(r"\[/?[a-z][^\]\n]*\]")

# Maximum characters kept in each display buffer (older content is trimmed).
_MAX_BUFFER_CHARS = 50_000

# Tokenizer pattern table for real-time TUI syntax highlighting
_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "class:token.tool",
        re.compile(
            r"\[(?:TOOL:[\w_-]+|SKILL[\w_-]*|REASONING|browse|bash|file_read|file_write|fetch_url)\]"
        ),
    ),
    (
        "class:token.success",
        re.compile(r"(?:✨\s*)?[✔✓]|\b(?:SUCCESS|SUCCESSFUL|DONE|OK)\b|\[OK\]|\[SUCCESS\]"),
    ),
    (
        "class:token.error",
        re.compile(
            r"(?:💥\s*)?[✖✗]|\b(?:ERROR|FAILED|FAIL|CRITICAL)\b|\b(?:Error|Failed):\b|\[ERROR\]"
        ),
    ),
    (
        "class:token.warning",
        re.compile(r"(?:⚠️\s*)?\b(?:WARNING|WARN|HINT|NOTICE)\b|\b(?:Warning|Hint):\b|\[WARNING\]"),
    ),
    (
        "class:token.state",
        re.compile(r"\b(?:PLANNING|PROCESSING|TOOL_EXECUTION|SYNTHESIZING|COMPLETED|INCOMPLETE)\b"),
    ),
    ("class:token.url", re.compile(r"https?://[^\s\"'<>]+")),
    ("class:token.path", re.compile(r"(?:/[a-zA-Z0-9_.-]+)+/[a-zA-Z0-9_.-]+")),
    (
        "class:token.metric",
        re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|tok/s|tokens|MB|KB|GB|%)\b|\$\d+(?:\.\d+)?"),
    ),
    ("class:token.prompt", re.compile(r"^[ \t]*[❯→●◆•]")),
    ("class:token.code", re.compile(r"`[^`]+`")),
]


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags, leaving plain text."""
    return _RICH_MARKUP.sub("", text)


class TUIKeywordLexer(Lexer):
    """Real-time regex keyword and token highlighter for TUI text areas."""

    def lex_document(self, document: Document) -> Callable[[int], list[tuple[str, str]]]:
        def get_line(lineno: int) -> list[tuple[str, str]]:
            line = document.lines[lineno]
            if not line:
                return [("", "")]

            # Find all matched intervals (start, end, style)
            matches: list[tuple[int, int, str]] = []
            for style, pattern in _TOKEN_PATTERNS:
                for m in pattern.finditer(line):
                    matches.append((m.start(), m.end(), style))

            if not matches:
                return [("", line)]

            # Sort matches by start position, favor longer spans on conflict
            matches.sort(key=lambda x: (x[0], -(x[1] - x[0])))

            result: list[tuple[str, str]] = []
            cur = 0
            for start, end, style in matches:
                if start < cur:
                    continue  # Overlaps with previous accepted match
                if start > cur:
                    result.append(("", line[cur:start]))
                result.append((style, line[start:end]))
                cur = end

            if cur < len(line):
                result.append(("", line[cur:]))

            return result

        return get_line


_TUI_STYLE = Style.from_dict(
    {
        # Top System Bar & Footers
        "header.system": "bg:#0b0f19 #38bdf8 bold",
        "header.shortcuts": "bg:#0b0f19 #94a3b8",
        # Tile Headers
        "header.thinking": "bg:#1e1438 #e0aaff bold",
        "header.log": "bg:#0d2621 #5eead4 bold",
        "header.input": "bg:#1f2937 #fbbf24 bold",
        # Section Dividers (labeled unicode frames between tiles)
        "border": "#334155",
        "border.log": "bg:#0d2621 #0d9488 bold",
        "border.input": "bg:#1f2937 #d97706 bold",
        # Text Areas Backgrounds & Foregrounds
        "text-area.thinking": "bg:#0e0d17 #e2e8f0",
        "text-area.log": "bg:#080c14 #e2e8f0",
        "text-area.input": "bg:#111827 #ffffff",
        # Lexer Tokens (Obsidian TrueColor)
        "token.tool": "#00f0ff bold",
        "token.success": "#10b981 bold",
        "token.error": "#f43f5e bold",
        "token.warning": "#f59e0b bold",
        "token.state": "#c084fc bold",
        "token.url": "#67e8f9 underline",
        "token.path": "#38bdf8 underline",
        "token.metric": "#fb923c bold",
        "token.prompt": "#38bdf8 bold",
        "token.code": "#a78bfa",
    }
)


class MouseAwareControl(FormattedTextControl):
    """FormattedTextControl with mouse scroll and click callback support."""

    def __init__(
        self,
        text: Any = "",
        on_scroll_up: Callable[[], None] | None = None,
        on_scroll_down: Callable[[], None] | None = None,
        on_click: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(text, **kwargs)
        self.on_scroll_up = on_scroll_up
        self.on_scroll_down = on_scroll_down
        self.on_click = on_click

    def mouse_handler(self, mouse_event: Any) -> Any:
        from prompt_toolkit.mouse_events import MouseEventType

        if mouse_event.event_type == MouseEventType.SCROLL_UP and self.on_scroll_up is not None:
            self.on_scroll_up()
            return None
        elif (
            mouse_event.event_type == MouseEventType.SCROLL_DOWN and self.on_scroll_down is not None
        ):
            self.on_scroll_down()
            return None
        elif mouse_event.event_type == MouseEventType.MOUSE_UP and self.on_click is not None:
            self.on_click()
            return None
        return super().mouse_handler(mouse_event)


class VibeTUI:
    """Full-screen tiled UI for Vibe Agent.

    Usage:
        tui = VibeTUI()
        app = tui.create_app()
        await app.run_async()
    """

    def __init__(self, history_path: str | None = None) -> None:
        self.lexer = TUIKeywordLexer()

        # Display areas (read-only TextArea with scrolling and focus support)
        self.thinking_area = TextArea(
            read_only=True,
            focusable=True,
            wrap_lines=True,
            scrollbar=True,
            lexer=self.lexer,
            style="class:text-area.thinking",
        )
        self.log_area = TextArea(
            read_only=True,
            focusable=True,
            wrap_lines=True,
            scrollbar=True,
            lexer=self.lexer,
            style="class:text-area.log",
        )

        # Input area: multiline so it can expand. Starts at 1 line; Ctrl-T
        # toggles it to half the screen height for editing long prompts.
        self._input_expanded = False
        input_kwargs: dict[str, Any] = {}
        if history_path:
            from prompt_toolkit.history import FileHistory

            input_kwargs["history"] = FileHistory(history_path)
        self.input_area = TextArea(
            prompt="❯ ",
            multiline=True,
            wrap_lines=True,
            height=self._input_height,
            style="class:text-area.input",
            **input_kwargs,
        )

        # Attach mouse scrolling and click focus to display & input areas
        self._attach_mouse_scrolling(
            self.thinking_area,
            lambda: self.scroll_thinking_up(count=3),
            lambda: self.scroll_thinking_down(count=3),
            on_click=lambda: self.layout.focus(self.thinking_area),
        )
        self._attach_mouse_scrolling(
            self.log_area,
            lambda: self.scroll_log_up(count=3),
            lambda: self.scroll_log_down(count=3),
            on_click=lambda: self.layout.focus(self.log_area),
        )
        self._attach_mouse_scrolling(
            self.input_area,
            self._scroll_input_up,
            self._scroll_input_down,
            on_click=lambda: self.layout.focus(self.input_area),
        )

        # Status shown across top bar and input section divider
        self._model_name = "default"
        self._system_state = "READY"
        self._status_text = "ready"
        self._metrics_summary = ""
        self._queue_text = ""

        # Layout: Top bar / thinking / log / input / Bottom shortcuts.
        # Each section seam is a labeled divider so boundaries stay obvious
        # even after long sessions.
        self.container = HSplit(
            [
                Window(
                    MouseAwareControl(
                        self._get_top_header,
                        on_click=lambda: self.layout.focus(self.input_area),
                    ),
                    height=1,
                    style="class:header.system",
                ),
                Window(
                    MouseAwareControl(
                        " 🧠 💭 AGENT THINKING STREAM ",
                        on_scroll_up=lambda: self.scroll_thinking_up(count=3),
                        on_scroll_down=lambda: self.scroll_thinking_down(count=3),
                        on_click=lambda: self.layout.focus(self.thinking_area),
                    ),
                    height=1,
                    style="class:header.thinking",
                ),
                self.thinking_area,
                self._make_divider(
                    "⚡ 🛠️ WORKING LOG & TOOL ACTIONS",
                    "class:border.log",
                    on_scroll_up=lambda: self.scroll_log_up(count=3),
                    on_scroll_down=lambda: self.scroll_log_down(count=3),
                    on_click=lambda: self.layout.focus(self.log_area),
                ),
                self.log_area,
                Window(
                    MouseAwareControl(
                        self._get_input_header,
                        on_scroll_up=self._scroll_input_up,
                        on_scroll_down=self._scroll_input_down,
                        on_click=lambda: self.layout.focus(self.input_area),
                    ),
                    height=1,
                    style="class:border.input",
                ),
                self.input_area,
                Window(
                    MouseAwareControl(
                        " 🚪 [Ctrl-C] Exit  │  📜 [↑/↓] History  │  🧹 [/clear] Reset  │"
                        "  ⤢ [Ctrl-T] Expand  │  📜 [PgUp/PgDn] Log  │"
                        "  💭 [Alt-PgUp/PgDn] Thinking",
                        on_scroll_up=lambda: self.scroll_log_up(count=3),
                        on_scroll_down=lambda: self.scroll_log_down(count=3),
                        on_click=lambda: self.layout.focus(self.input_area),
                    ),
                    height=1,
                    style="class:header.shortcuts",
                ),
            ],
            key_bindings=self._make_kb(),
        )

        self.layout = Layout(self.container, focused_element=self.input_area)

        self._app: Application | None = None
        self._submit_callback: Callable[[str], None] | None = None
        self._history_index: int | None = None
        self._current_input: str = ""

    def _attach_mouse_scrolling(
        self,
        area: TextArea,
        on_scroll_up: Callable[[], None],
        on_scroll_down: Callable[[], None],
        on_click: Callable[[], None] | None = None,
    ) -> None:
        """Attach mouse wheel scrolling and click focus to a TextArea control."""
        from prompt_toolkit.mouse_events import MouseEventType

        original_handler = area.control.mouse_handler

        def custom_mouse_handler(mouse_event: Any) -> Any:
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                on_scroll_up()
                return None
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                on_scroll_down()
                return None
            elif mouse_event.event_type == MouseEventType.MOUSE_UP and on_click is not None:
                on_click()
            return original_handler(mouse_event)

        area.control.mouse_handler = custom_mouse_handler

    def _scroll_input_up(self) -> None:
        """Scroll multiline input up or browse command history backward."""
        if self._input_expanded or "\n" in self.input_area.text:
            self.input_area.buffer.cursor_up(count=1)
            self._invalidate()
        else:
            self._history_backward()

    def _scroll_input_down(self) -> None:
        """Scroll multiline input down or browse command history forward."""
        if self._input_expanded or "\n" in self.input_area.text:
            self.input_area.buffer.cursor_down(count=1)
            self._invalidate()
        else:
            self._history_forward()

    def _input_height(self) -> int:
        """Input area height: 1 line collapsed, half the screen when expanded.

        Called by the layout engine on every render, so the expanded height
        tracks terminal resizes.
        """
        if not self._input_expanded:
            return 1
        rows = 24
        if self._app is not None:
            try:
                rows = self._app.output.get_size().rows
            except Exception:
                pass
        return max(3, rows // 2)

    def _toggle_input_expanded(self) -> None:
        """Toggle the input area between 1 line and half-screen height."""
        self._input_expanded = not self._input_expanded
        self._invalidate()

    def scroll_log_up(self, count: int = 10) -> None:
        """Scroll the working log area upwards."""
        self.log_area.buffer.cursor_up(count=count)
        self._invalidate()

    def scroll_log_down(self, count: int = 10) -> None:
        """Scroll the working log area downwards."""
        self.log_area.buffer.cursor_down(count=count)
        self._invalidate()

    def scroll_thinking_up(self, count: int = 10) -> None:
        """Scroll the agent thinking area upwards."""
        self.thinking_area.buffer.cursor_up(count=count)
        self._invalidate()

    def scroll_thinking_down(self, count: int = 10) -> None:
        """Scroll the agent thinking area downwards."""
        self.thinking_area.buffer.cursor_down(count=count)
        self._invalidate()

    def _history_backward(self) -> None:
        """Recall the previous command from history (Up arrow / Ctrl-P)."""
        history = list(self.input_area.buffer.history.get_strings())
        if not history:
            return
        if self._history_index is None:
            self._current_input = self.input_area.text
            self._history_index = len(history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self.input_area.text = history[self._history_index]
        self.input_area.buffer.cursor_position = len(self.input_area.text)

    def _history_forward(self) -> None:
        """Recall the next command from history (Down arrow / Ctrl-N)."""
        history = list(self.input_area.buffer.history.get_strings())
        if self._history_index is None:
            return
        if self._history_index < len(history) - 1:
            self._history_index += 1
            self.input_area.text = history[self._history_index]
            self.input_area.buffer.cursor_position = len(self.input_area.text)
        else:
            self._history_index = None
            self.input_area.text = self._current_input
            self.input_area.buffer.cursor_position = len(self.input_area.text)

    def _make_kb(self) -> KeyBindings:
        """Create key bindings."""
        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-q")
        def _(event: Any) -> None:
            event.app.exit()

        @kb.add("up")
        @kb.add("c-p")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.log_area):
                self.scroll_log_up(count=1)
            elif self.layout.has_focus(self.thinking_area):
                self.scroll_thinking_up(count=1)
            else:
                self._history_backward()

        @kb.add("down")
        @kb.add("c-n")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.log_area):
                self.scroll_log_down(count=1)
            elif self.layout.has_focus(self.thinking_area):
                self.scroll_thinking_down(count=1)
            else:
                self._history_forward()

        @kb.add("pageup")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.thinking_area):
                self.scroll_thinking_up(count=10)
            else:
                self.scroll_log_up(count=10)

        @kb.add("pagedown")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.thinking_area):
                self.scroll_thinking_down(count=10)
            else:
                self.scroll_log_down(count=10)

        @kb.add("s-pageup")
        @kb.add("c-pageup")
        @kb.add("escape", "pageup")
        @kb.add("c-u")
        def _(event: Any) -> None:
            self.scroll_thinking_up(count=10)

        @kb.add("s-pagedown")
        @kb.add("c-pagedown")
        @kb.add("escape", "pagedown")
        @kb.add("c-d")
        def _(event: Any) -> None:
            self.scroll_thinking_down(count=10)

        @kb.add("tab")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.input_area):
                self.layout.focus(self.log_area)
            elif self.layout.has_focus(self.log_area):
                self.layout.focus(self.thinking_area)
            else:
                self.layout.focus(self.input_area)

        @kb.add("s-tab")
        def _(event: Any) -> None:
            if self.layout.has_focus(self.input_area):
                self.layout.focus(self.thinking_area)
            elif self.layout.has_focus(self.thinking_area):
                self.layout.focus(self.log_area)
            else:
                self.layout.focus(self.input_area)

        @kb.add("escape")
        def _(event: Any) -> None:
            if not self.layout.has_focus(self.input_area):
                self.layout.focus(self.input_area)

        @kb.add("c-t")
        def _(event: Any) -> None:
            self._toggle_input_expanded()

        @kb.add("enter")
        def _(event: Any) -> None:
            if not self.layout.has_focus(self.input_area):
                self.layout.focus(self.input_area)
                return
            text = self.input_area.text.strip()
            self._history_index = None
            self._current_input = ""
            if text:
                self.input_area.buffer.history.append_string(text)
                self._on_submit(text)
            self.input_area.text = ""

        @kb.add("escape", "enter")
        def _(event: Any) -> None:
            # Alt-Enter inserts a newline while the input area is expanded.
            if self._input_expanded and self.layout.has_focus(self.input_area):
                self.input_area.buffer.insert_text("\n")

        return kb

    def _make_divider(
        self,
        label: str,
        style: str = "class:border",
        on_scroll_up: Callable[[], None] | None = None,
        on_scroll_down: Callable[[], None] | None = None,
        on_click: Callable[[], None] | None = None,
    ) -> Window:
        """Return a 1-line labeled unicode divider marking a section boundary."""
        return Window(
            MouseAwareControl(
                f"╞══ {label} " + "═" * 200 + "╡",
                on_scroll_up=on_scroll_up,
                on_scroll_down=on_scroll_down,
                on_click=on_click,
            ),
            height=1,
            style=style,
        )

    def _get_top_header(self) -> HTML:
        """Dynamic top system bar with logo, model, state, and metrics."""
        state_icon = "🟢" if self._system_state == "READY" else "⚡"
        parts = [
            "<b><style color='#38bdf8'>🌌 ◆ VIBE AGENT</style></b>",
            f"<style color='#c084fc'>🤖 [model: {html.escape(self._model_name)}]</style>",
            f"<style color='#34d399'>{state_icon} [● {html.escape(self._system_state)}]</style>",
        ]
        if self._metrics_summary:
            metric_escaped = html.escape(self._metrics_summary)
            parts.append(f"<style color='#fb923c'>📊 [{metric_escaped}]</style>")
        return HTML(f" {'  ──  '.join(parts)} ")

    def _get_input_header(self) -> HTML:
        """Dynamic input section divider with live status, queue, expand hint."""
        parts = [self._status_text]
        if self._queue_text:
            parts.append(f"📬 {self._queue_text}")
        parts.append("⤢ [Ctrl-T] Collapse" if self._input_expanded else "⤢ [Ctrl-T] Expand")
        # User-derived text (queue preview, model names) can contain HTML
        # metacharacters, so escape before handing to the HTML parser.
        body = html.escape(" │ ".join(parts))
        return HTML(f"╞══ ⌨️ 💬 USER PROMPT │ {body} " + "═" * 200 + "╡")

    def set_model(self, model: str) -> None:
        """Update active model badge in the top bar."""
        if model != self._model_name:
            self._model_name = model
            self._invalidate()

    def set_system_state(self, state: str) -> None:
        """Update system state (e.g. READY, BUSY, THINKING)."""
        if state != self._system_state:
            self._system_state = state
            self._invalidate()

    def set_metrics(
        self, total_tokens: int, tokens_per_second: float, cost: float | None = None
    ) -> None:
        """Update metrics summary shown in the top bar."""
        tok_str = f"{total_tokens} tokens"
        speed_str = f"{tokens_per_second:.1f} tok/s"
        cost_str = f" │ ${cost:.4f}" if cost is not None else ""
        summary = f"{tok_str} │ ⚡ {speed_str}{cost_str}"
        if summary != self._metrics_summary:
            self._metrics_summary = summary
            self._invalidate()

    def set_status(self, text: str) -> None:
        """Update the status shown in the input section divider."""
        if text != self._status_text:
            self._status_text = text
            self._invalidate()

    def set_queue_info(self, pending: int, next_msg: str | None = None) -> None:
        """Update queue pending count and next message preview."""
        if pending == 0:
            queue_text = ""
        else:
            preview = (
                f": {next_msg[:30]}..."
                if next_msg and len(next_msg) > 30
                else (f": {next_msg}" if next_msg else "")
            )
            queue_text = f"queue:{pending}{preview}"
        if queue_text != self._queue_text:
            self._queue_text = queue_text
            self._invalidate()

    def _on_submit(self, text: str) -> None:
        """Handle user input submission."""
        if self._submit_callback:
            self._submit_callback(text)
        # Echo to log area for immediate feedback
        self.append_log(f"❯ {text}")

    def set_submit_callback(self, callback: Callable[[str], None]) -> None:
        """Set the callback invoked when user submits input."""
        self._submit_callback = callback

    def _invalidate(self) -> None:
        """Trigger a UI redraw if the app is running."""
        if self._app is not None:
            self._app.invalidate()

    def exit(self) -> None:
        """Request the application to exit."""
        if self._app is not None:
            self._app.exit()

    def _append_capped(self, area: TextArea, text: str) -> None:
        """Append text to a TextArea, trimming old content to bound growth."""
        new_text = area.text + text
        if len(new_text) > _MAX_BUFFER_CHARS:
            # Keep the tail; snap to the next line boundary to avoid mid-line cut.
            tail = new_text[-_MAX_BUFFER_CHARS:]
            nl = tail.find("\n")
            new_text = tail[nl + 1 :] if 0 <= nl < 200 else tail
        area.text = new_text
        area.buffer.cursor_position = len(area.text)
        self._invalidate()

    def append_thinking(self, text: str) -> None:
        """Append text to the thinking tile and scroll to bottom."""
        self._append_capped(self.thinking_area, _strip_markup(text))

    def append_log(self, text: str) -> None:
        """Append a line to the working log tile and scroll to bottom."""
        self._append_capped(self.log_area, _strip_markup(text) + "\n")

    def append_log_chunk(self, text: str) -> None:
        """Append a stream chunk to the working log without a trailing newline."""
        self._append_capped(self.log_area, _strip_markup(text))

    def clear_thinking(self) -> None:
        """Clear the thinking tile."""
        self.thinking_area.text = ""
        self._invalidate()

    def clear_log(self) -> None:
        """Clear the working log tile."""
        self.log_area.text = ""
        self._invalidate()

    def create_app(self) -> Application:
        """Build and return the prompt_toolkit Application."""
        self._app = Application(
            layout=self.layout,
            full_screen=True,
            mouse_support=True,
            style=_TUI_STYLE,
        )
        return self._app

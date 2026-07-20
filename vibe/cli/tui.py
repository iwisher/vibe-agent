"""Tiled terminal UI for Vibe Agent interactive mode.

Provides a full-screen prompt_toolkit layout with:
- Top tile: agent thinking / reasoning stream
- Middle tile: working log / tool results / metrics
- Bottom tile: user input box with live status in its title
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

# Match Rich-style tags but never across newlines, so an unmatched "[" does not
# swallow the rest of the buffer.
_RICH_MARKUP = re.compile(r"\[/?[a-z][^\]\n]*\]")

# Maximum characters kept in each display buffer (older content is trimmed).
_MAX_BUFFER_CHARS = 50_000


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags, leaving plain text."""
    return _RICH_MARKUP.sub("", text)


_TUI_STYLE = Style.from_dict(
    {
        "header.thinking": "bg:#2d1b4e #e0aaff bold",
        "header.log": "bg:#1b3a2d #a8dadc bold",
        "header.input": "bg:#3a2a1b #ffd166 bold",
        "border": "#555555",
    }
)


class VibeTUI:
    """Full-screen tiled UI for Vibe Agent.

    Usage:
        tui = VibeTUI()
        app = tui.create_app()
        await app.run_async()
    """

    def __init__(self, history_path: str | None = None) -> None:
        # Display areas (read-only TextArea handles scrolling automatically)
        self.thinking_area = TextArea(
            read_only=True,
            focusable=False,
            wrap_lines=True,
            scrollbar=True,
        )
        self.log_area = TextArea(
            read_only=True,
            focusable=False,
            wrap_lines=True,
            scrollbar=True,
        )

        # Input area, with optional up-arrow history recall
        input_kwargs: dict[str, Any] = {}
        if history_path:
            from prompt_toolkit.history import FileHistory

            input_kwargs["history"] = FileHistory(history_path)
        self.input_area = TextArea(
            prompt="❯ ",
            multiline=False,
            wrap_lines=False,
            **input_kwargs,
        )

        # Status shown in the input tile header
        self._status_text = "ready"
        self._queue_text = ""

        # Layout: thinking (40%) / log (40%) / input (20%)
        self.container = HSplit(
            [
                Window(
                    FormattedTextControl(" Agent Thinking "),
                    height=1,
                    style="class:header.thinking",
                ),
                self.thinking_area,
                self._make_border(),
                Window(
                    FormattedTextControl(" Working Log "),
                    height=1,
                    style="class:header.log",
                ),
                self.log_area,
                self._make_border(),
                Window(
                    FormattedTextControl(self._get_input_header),
                    height=1,
                    style="class:header.input",
                ),
                self.input_area,
            ],
            key_bindings=self._make_kb(),
        )

        self.layout = Layout(self.container, focused_element=self.input_area)

        self._app: Application | None = None
        self._submit_callback: Callable[[str], None] | None = None

    def _make_kb(self) -> KeyBindings:
        """Create key bindings."""
        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-q")
        def _(event: Any) -> None:
            event.app.exit()

        @kb.add("enter")
        def _(event: Any) -> None:
            text = self.input_area.text.strip()
            if text:
                self._on_submit(text)
            self.input_area.text = ""

        return kb

    def _make_border(self) -> Window:
        """Return a 1-line horizontal border between tiles."""
        return Window(
            FormattedTextControl("─" * 200),
            height=1,
            style="class:border",
        )

    def _get_input_header(self) -> HTML:
        """Dynamic input tile header with live status and queue info."""
        parts = [self._status_text]
        if self._queue_text:
            parts.append(self._queue_text)
        # User-derived text (queue preview, model names) can contain HTML
        # metacharacters, so escape before handing to the HTML parser.
        return HTML(f" Input │ {html.escape(' │ '.join(parts))} ")

    def set_status(self, text: str) -> None:
        """Update the status shown in the input tile header."""
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
            mouse_support=False,
            style=_TUI_STYLE,
        )
        return self._app

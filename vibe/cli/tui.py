"""Tiled terminal UI for Vibe Agent interactive mode.

Provides a full-screen prompt_toolkit layout with:
- Top tile: agent thinking / reasoning stream
- Middle tile: working log / tool results / metrics
- Bottom tile: user input box with live status in its title
"""

from __future__ import annotations

import re
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

_RICH_MARKUP = re.compile(r"\[/?[a-z][^\]]*\]")


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
        await tui.run()
    """

    def __init__(self) -> None:
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

        # Input area
        self.input_area = TextArea(
            prompt="❯ ",
            multiline=False,
            wrap_lines=False,
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
        return HTML(f" Input │ {' │ '.join(parts)} ")

    def set_status(self, text: str) -> None:
        """Update the status shown in the input tile header."""
        self._status_text = text
        self._invalidate()

    def set_queue_info(self, pending: int, next_msg: str | None = None) -> None:
        """Update queue pending count and next message preview."""
        if pending == 0:
            self._queue_text = ""
        else:
            preview = f": {next_msg[:30]}..." if next_msg else ""
            self._queue_text = f"queue:{pending}{preview}"
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

    def focus_input(self) -> None:
        """Ensure the input window has focus."""
        if self._app is not None:
            self._app.layout.focus(self.input_area)

    def append_thinking(self, text: str) -> None:
        """Append text to the thinking tile and scroll to bottom."""
        self.thinking_area.text += _strip_markup(text)
        self.thinking_area.buffer.cursor_position = len(self.thinking_area.text)
        self._invalidate()

    def append_log(self, text: str) -> None:
        """Append text to the working log tile and scroll to bottom."""
        self.log_area.text += _strip_markup(text) + "\n"
        self.log_area.buffer.cursor_position = len(self.log_area.text)
        self._invalidate()

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

    async def run(self) -> None:
        """Run the TUI application."""
        app = self.create_app()
        await app.run_async()

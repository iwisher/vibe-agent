"""Tiled terminal UI for Vibe Agent interactive mode.

Provides a full-screen prompt_toolkit layout with:
- Top tile: agent thinking / reasoning stream
- Middle tile: working log / tool results / metrics
- Bottom tile: user input box
"""

from __future__ import annotations

import re
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import TextArea

_RICH_MARKUP = re.compile(r"\[/?[a-z][^\]]*\]")


def _strip_markup(text: str) -> str:
    """Remove Rich markup tags, leaving plain text."""
    return _RICH_MARKUP.sub("", text)


class VibeTUI:
    """Full-screen tiled UI for Vibe Agent.

    Usage:
        tui = VibeTUI()
        await tui.run()
    """

    def __init__(self) -> None:
        # Scrollable read-only buffers
        self.thinking_buffer = Buffer()
        self.log_buffer = Buffer()

        # Input area
        self.input_area = TextArea(
            prompt="❯ ",
            multiline=False,
            wrap_lines=False,
        )

        # Layout: thinking (40%) / log (40%) / input (20%)
        thinking_window = Window(
            BufferControl(buffer=self.thinking_buffer),
            height=Dimension(weight=4),
            wrap_lines=True,
        )
        log_window = Window(
            BufferControl(buffer=self.log_buffer),
            height=Dimension(weight=4),
            wrap_lines=True,
        )
        input_window = Window(
            BufferControl(buffer=self.input_area.buffer),
            height=Dimension(weight=2),
        )

        self.container = HSplit(
            [
                Window(
                    FormattedTextControl("Agent Thinking"),
                    height=1,
                    style="class:header",
                ),
                thinking_window,
                Window(
                    FormattedTextControl("Working Log"),
                    height=1,
                    style="class:header",
                ),
                log_window,
                Window(
                    FormattedTextControl("Input"),
                    height=1,
                    style="class:header",
                ),
                input_window,
            ]
        )

        self.layout = Layout(self.container, focused_element=input_window)

        # Key bindings
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

        self.kb = kb
        self._app: Application | None = None
        self._submit_callback: Callable[[str], None] | None = None

    def _on_submit(self, text: str) -> None:
        """Handle user input submission."""
        if self._submit_callback:
            self._submit_callback(text)
        # Echo to log buffer for immediate feedback
        self.append_log(f"❯ {text}")

    def set_submit_callback(self, callback: Callable[[str], None]) -> None:
        """Set the callback invoked when user submits input."""
        self._submit_callback = callback

    def append_thinking(self, text: str) -> None:
        """Append text to the thinking tile."""
        self.thinking_buffer.insert_text(_strip_markup(text))

    def append_log(self, text: str) -> None:
        """Append text to the working log tile."""
        self.log_buffer.insert_text(_strip_markup(text) + "\n")

    def clear_thinking(self) -> None:
        """Clear the thinking tile."""
        self.thinking_buffer.reset()

    def clear_log(self) -> None:
        """Clear the working log tile."""
        self.log_buffer.reset()

    def create_app(self) -> Application:
        """Build and return the prompt_toolkit Application."""
        self._app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            full_screen=True,
            mouse_support=False,
        )
        return self._app

    async def run(self) -> None:
        """Run the TUI application."""
        app = self.create_app()
        await app.run_async()

"""Shared Rich rendering helpers for the CLI console surfaces.

All console paths (legacy interactive branch, SessionController output
consumer, and single-shot mode) render assistant responses, tool results,
errors, and metrics through these helpers so behavior stays consistent.
The full-screen TUI reuses only the plain-text helpers (truncate_output,
stringify_content, format_tool_result_text) because prompt_toolkit
TextAreas cannot render Rich objects.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# Maximum characters shown in a tool result panel before truncating.
MAX_TOOL_OUTPUT_CHARS = 2000

# Maximum characters of the tool args summary shown in a panel title.
_ARGS_SUMMARY_CHARS = 60


def stringify_content(content: Any) -> str:
    """Convert tool result content into displayable text.

    Dicts and lists are dumped as indented JSON; everything else is
    stringified. None becomes the empty string.

    Args:
        content: Arbitrary tool result payload.

    Returns:
        A printable string, never None.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, indent=2, default=str)
    except (TypeError, ValueError):
        return str(content)


def truncate_output(text: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """Truncate long tool output with an explicit omission note.

    Args:
        text: The text to truncate.
        max_chars: Maximum characters of the original text to keep.

    Returns:
        The original text when short enough, otherwise its prefix plus a
        "… truncated (N more chars)" note where N is the number of
        omitted characters.
    """
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return f"{text[:max_chars]}\n… truncated ({omitted} more chars)"


def render_response(console: Console, text: str) -> None:
    """Render a final assistant response as markdown.

    Args:
        console: The Rich console to print to.
        text: The complete assistant response (markdown source).
    """
    if not text:
        return
    console.print(Markdown(text))


def safe_print_chunk(console: Console, chunk: str, style: str | None = None) -> None:
    """Print a streamed chunk without interpreting Rich markup.

    LLM output frequently contains square brackets that Rich would
    otherwise parse as markup tags and swallow.

    Args:
        console: The Rich console to print to.
        chunk: A streamed text fragment; printed without a trailing newline.
        style: Optional Rich style applied to the whole chunk (e.g. "dim"
            for reasoning tokens).
    """
    if not chunk:
        return
    console.print(chunk, end="", markup=False, highlight=False, style=style)


def _args_summary(args: dict[str, Any] | None) -> str:
    """Build a short one-line summary of tool call arguments."""
    if not args:
        return ""
    parts = []
    for key, value in args.items():
        rendered = str(value).replace("\n", " ")
        if len(rendered) > 24:
            rendered = rendered[:21] + "..."
        parts.append(f"{key}={rendered}")
    summary = ", ".join(parts)
    if len(summary) > _ARGS_SUMMARY_CHARS:
        summary = summary[: _ARGS_SUMMARY_CHARS - 3] + "..."
    return summary


def _guess_lexer(text: str) -> str | None:
    """Best-effort language guess for syntax highlighting tool output."""
    stripped = text.strip()
    if not stripped:
        return None
    if stripped[0] in "[{":
        try:
            json.loads(stripped)
            return "json"
        except ValueError:
            pass
    first_line = stripped.split("\n", 1)[0]
    if first_line.startswith(("def ", "import ", "from ", "class ")):
        return "python"
    if first_line.startswith(("diff ", "--- ")):
        return "diff"
    return None


def render_tool_result(
    console: Console,
    *,
    name: str | None = None,
    args: dict[str, Any] | None = None,
    content: Any,
    is_error: bool,
    duration_s: float | None = None,
) -> None:
    """Render one tool result as a structured panel.

    The panel header shows the tool name (falling back to a generic
    "Tool Result"/"Tool Error" title), a short args summary, and the
    execution duration when available. The body is truncated to
    MAX_TOOL_OUTPUT_CHARS and syntax-highlighted when it looks like
    JSON or code. Errors use a red border.

    Args:
        console: The Rich console to print to.
        name: Tool name, or None for a generic title.
        args: Tool call arguments, summarized into the title.
        content: Tool result payload (or error text).
        is_error: Whether the result represents a failure.
        duration_s: Execution duration in seconds, when known.
    """
    text = truncate_output(stringify_content(content))
    # The title is markup-parsed, so escape every dynamic part; an args
    # summary like [command=...] would otherwise be eaten as a style tag.
    title = escape(name) if name else ("Tool Error" if is_error else "Tool Result")
    summary = _args_summary(args)
    if summary:
        title = f"{title} ({escape(summary)})"
    if duration_s is not None:
        title = f"{title} — {duration_s:.1f}s"
    lexer = _guess_lexer(text)
    body: Any = Syntax(text, lexer, theme="ansi_dark", word_wrap=True) if lexer else Text(text)
    console.print(Panel(body, title=title, border_style="red" if is_error else "green"))


def render_tool_result_from_metadata(console: Console, tr: Any) -> None:
    """Render a ToolResult, reading name/args/duration from its metadata.

    Falls back to the generic "Tool Result"/"Tool Error" titles when the
    metadata keys are absent.

    Args:
        console: The Rich console to print to.
        tr: A ToolResult (duck-typed: success/content/error/metadata).
    """
    meta = getattr(tr, "metadata", None) or {}
    render_tool_result(
        console,
        name=meta.get("tool_name"),
        args=meta.get("tool_args"),
        content=tr.content if tr.content else (getattr(tr, "error", None) or ""),
        is_error=not tr.success,
        duration_s=meta.get("duration_s"),
    )


def format_tool_result_text(tr: Any, max_chars: int = 1000) -> str:
    """Format a ToolResult as an emoji-rich text block for the TUI log tile.

    Args:
        tr: A ToolResult (duck-typed: success/content/error/metadata).
        max_chars: Truncation limit for the result body.

    Returns:
        A formatted string with emoji status and tool tag.
    """
    meta = getattr(tr, "metadata", None) or {}
    name = meta.get("tool_name") or "tool"
    status_icon = "✨ ✔" if tr.success else "💥 ✖"
    body = tr.content if tr.content else (getattr(tr, "error", None) or "")
    text = truncate_output(stringify_content(body), max_chars)
    duration = meta.get("duration_s")
    dur_str = f" ({duration:.2f}s)" if duration is not None else ""
    return f"{status_icon} [TOOL:{name}]{dur_str}: {text}"


def render_error(
    console: Console,
    *,
    message: str,
    hint: str | None = None,
    model: str | None = None,
) -> None:
    """Render an error as a red panel, preserving hint and model info.

    Args:
        console: The Rich console to print to.
        message: The error message.
        hint: Optional actionable hint shown below the message.
        model: Optional name of the model that produced the error.
    """
    parts = [escape(str(message))]
    if hint:
        parts.append(f"\n\n[bold]Hint:[/bold] {escape(str(hint))}")
    if model:
        parts.append(f"\n\n[bold]Model Used:[/bold] {escape(str(model))}")
    console.print(Panel("".join(parts), title="Error", border_style="red"))


def format_metrics_line(
    metrics: Any,
    *,
    model_used: str | None = None,
    current_model: str | None = None,
    session_cost: float | None = None,
) -> str:
    """Format the standard dim metrics line.

    Produces "N tokens (r reasoning) | Xs | Y tok/s" plus an optional
    cumulative session cost and fallback-model note.

    Args:
        metrics: A Metrics object (duck-typed token/timing fields).
        model_used: Model that actually answered (after fallback).
        current_model: The session's configured default model.
        session_cost: Cumulative session cost, when reachable.

    Returns:
        The formatted metrics string (markup-free).
    """
    reasoning_part = ""
    if getattr(metrics, "reasoning_tokens", 0) > 0:
        reasoning_part = f" ({metrics.reasoning_tokens} reasoning)"
    line = (
        f"{metrics.total_tokens} tokens{reasoning_part} | "
        f"{metrics.elapsed_seconds:.1f}s | "
        f"{metrics.tokens_per_second:.1f} tok/s"
    )
    if session_cost:
        line += f" | ${session_cost:.4f}"
    if model_used and current_model and model_used != current_model:
        line += f" (via fallback model: {model_used})"
    return line


def get_session_cost(query_loop: Any) -> float | None:
    """Return the cumulative session cost when a spend tracker is reachable.

    Reads ``query_loop.cost_router.spend_tracker`` defensively; the cost
    router is only wired when ``cost_router.enabled`` is set in the config
    and is constructed without a spend tracker today, so this normally
    returns None (no cost shown) rather than failing.

    Args:
        query_loop: A QueryLoop (or sub-agent main loop).

    Returns:
        The cumulative session cost, or None when unreachable.
    """
    router = getattr(query_loop, "cost_router", None)
    tracker = getattr(router, "spend_tracker", None) if router is not None else None
    if tracker is None:
        return None
    try:
        spend = tracker.get_spend(getattr(router, "session_id", None) or "default")
        return float(spend.get("total_cost", 0.0))
    except Exception:
        return None


def format_shortcuts_help() -> str:
    """Return a formatted reference of all keyboard shortcuts and slash commands."""
    return """⌨️  VIBE AGENT SHORTCUTS & COMMANDS REFERENCE

Navigation & History:
  • PageUp / PageDown            Scroll Working Log history (10 lines)
  • Alt-PageUp / Alt-PageDown    Scroll Agent Thinking stream (10 lines)
    (or Shift-PageUp/Down, Ctrl-U/D)
  • Tab / Shift-Tab              Cycle focus between Input ↔ Log ↔ Thinking
  • Up / Down (in pane)          Line-by-line scroll in focused pane
  • Escape / Enter (in pane)     Return focus to user prompt

Input & Editing:
  • Ctrl-T                       Expand / Collapse prompt tile (up to 50% screen)
  • Alt-Enter                    Insert newline while prompt is expanded
  • Up / Down (in prompt)        Browse command history (Ctrl-P / Ctrl-N)
  • Enter (in prompt)            Submit prompt to agent

Control:
  • Ctrl-C / Ctrl-Q              Exit application

Slash Commands:
  • /shortcuts, /help, /keys     Display this cheat sheet
  • /clear                       Clear session history and reset buffers
  • /verbose                     Toggle verbose metrics and debug traces
  • /reasoning                   Toggle streaming of thinking tokens
  • /resume                      Resume the latest saved session checkpoint
  • /bg <query>                  Execute a sub-query in background
  • /btw <query>                 Ask a side-question without interrupting task
  • /queue <prompt>              Queue a follow-up prompt
  • /exit, exit, quit            Exit session"""

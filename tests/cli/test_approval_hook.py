"""Tests for the prompt_toolkit-aware approval UI hook in vibe.cli.main.

The hook renders approval prompts via `run_in_terminal` so they suspend and
redraw the prompt_toolkit app instead of overlapping the input area. These
tests pin the registration lifecycle in both interactive modes and the hook's
fail-closed behavior, using mocks — no real terminal (CI is headless).
"""

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

import vibe.cli.main as cli_main


@pytest.fixture
def clean_approval_ctx():
    """Guarantee the shared terminal context is reset around each test."""
    cli_main._approval_ctx["app"] = None
    cli_main._approval_ctx["loop"] = None
    yield
    cli_main._approval_ctx["app"] = None
    cli_main._approval_ctx["loop"] = None


@pytest.fixture
def bg_loop():
    """A real event loop running on a background thread (as the CLI's would)."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


class FakeController:
    """Minimal SessionController stand-in for interactive_mode tests."""

    def __init__(self):
        self.output_queue = asyncio.Queue()
        self.queue = SimpleNamespace(pending_count=0)
        self.main_loop = SimpleNamespace(
            config=SimpleNamespace(llm=SimpleNamespace(show_reasoning=True))
        )
        self.prompt_shown = False
        self.shutdown_called = False

    async def start(self):
        pass

    async def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def hook_recorders(monkeypatch):
    """Replace set/reset_approval_ui_hook with recorders; capture ctx at set."""
    calls = {"set": [], "reset": 0}

    def fake_set(fn):
        calls["set"].append((fn, dict(cli_main._approval_ctx)))

    def fake_reset():
        calls["reset"] += 1

    monkeypatch.setattr(cli_main, "set_approval_ui_hook", fake_set)
    monkeypatch.setattr(cli_main, "reset_approval_ui_hook", fake_reset)
    monkeypatch.setattr(cli_main, "_setup_readline_history", lambda: None)
    monkeypatch.setattr(cli_main, "_save_readline_history", lambda: None)
    return calls


class TestHookFactory:
    def test_returns_none_without_app(self, clean_approval_ctx):
        cli_main._approval_ctx["app"] = None
        cli_main._approval_ctx["loop"] = None
        hook = cli_main._make_pt_approval_hook()
        assert hook("rm a", None, "", "warning", "/tmp", 5) is None

    def test_returns_none_when_loop_closed(self, clean_approval_ctx):
        closed = asyncio.new_event_loop()
        closed.close()
        cli_main._approval_ctx["app"] = MagicMock()
        cli_main._approval_ctx["loop"] = closed
        hook = cli_main._make_pt_approval_hook()
        assert hook("rm a", None, "", "warning", "/tmp", 5) is None

    def test_schedules_on_loop_and_returns_token(self, clean_approval_ctx, bg_loop):
        sentinel_app = MagicMock(name="app")
        cli_main._approval_ctx["app"] = sentinel_app
        cli_main._approval_ctx["loop"] = bg_loop
        seen = {}

        async def fake_run_in_terminal(func, render_cli_done=False, in_executor=False):
            seen["in_executor"] = in_executor
            return func()

        @contextlib.contextmanager
        def fake_set_app(app):
            seen["app"] = app
            yield

        with (
            patch("prompt_toolkit.application.run_in_terminal", fake_run_in_terminal),
            patch("prompt_toolkit.application.current.set_app", fake_set_app),
            patch.object(cli_main, "render_and_read_choice", return_value="session") as rrc,
        ):
            hook = cli_main._make_pt_approval_hook()
            result = hook("rm a", "p1", "desc", "warning", "/tmp", 5)

        assert result == "session"
        assert seen["app"] is sentinel_app
        assert seen["in_executor"] is True
        rrc.assert_called_once_with("rm a", "p1", "desc", "warning", 5)

    def test_failure_is_fail_closed_never_legacy(self, clean_approval_ctx, bg_loop):
        cli_main._approval_ctx["app"] = MagicMock()
        cli_main._approval_ctx["loop"] = bg_loop

        async def boom(func, render_cli_done=False, in_executor=False):
            raise RuntimeError("renderer wedged")

        with (
            patch("prompt_toolkit.application.run_in_terminal", boom),
            patch.object(cli_main, "render_and_read_choice") as rrc,
        ):
            hook = cli_main._make_pt_approval_hook()
            result = hook("rm a", "p1", "desc", "warning", "/tmp", 5)

        assert result == "timeout"
        rrc.assert_not_called()


class TestConsoleModeRegistration:
    async def test_hook_registered_and_unregistered(
        self, monkeypatch, clean_approval_ctx, hook_recorders
    ):
        sentinel_app = MagicMock(name="prompt_app")
        fake_session = MagicMock()
        fake_session.app = sentinel_app

        async def eof_input(*args, **kwargs):
            raise EOFError

        monkeypatch.setattr(cli_main, "SessionController", FakeController)
        monkeypatch.setattr(cli_main, "get_prompt_session", lambda *a: fake_session)
        monkeypatch.setattr(cli_main, "prompt_input", eof_input)
        monkeypatch.setattr(cli_main, "get_patch_stdout", contextlib.nullcontext)
        # interactive_mode temporarily rebinds console.file to sys.stdout;
        # give it a throwaway Console so the module one is never mutated.
        monkeypatch.setattr(cli_main, "console", Console())

        controller = FakeController()
        await cli_main.interactive_mode(controller)

        calls = hook_recorders
        assert len(calls["set"]) == 1
        hook_fn, ctx_at_set = calls["set"][0]
        assert callable(hook_fn)
        assert ctx_at_set["app"] is sentinel_app
        assert ctx_at_set["loop"] is not None
        # Unregistered and context cleared on exit.
        assert calls["reset"] == 1
        assert cli_main._approval_ctx == {"app": None, "loop": None}
        assert controller.shutdown_called

    async def test_no_prompt_session_no_registration(
        self, monkeypatch, clean_approval_ctx, hook_recorders
    ):
        async def eof_input(*args, **kwargs):
            raise EOFError

        monkeypatch.setattr(cli_main, "SessionController", FakeController)
        monkeypatch.setattr(cli_main, "get_prompt_session", lambda *a: None)
        monkeypatch.setattr(cli_main, "prompt_input", eof_input)
        monkeypatch.setattr(cli_main, "get_patch_stdout", contextlib.nullcontext)

        controller = FakeController()
        await cli_main.interactive_mode(controller)

        calls = hook_recorders
        assert calls["set"] == []
        assert calls["reset"] == 1
        assert cli_main._approval_ctx == {"app": None, "loop": None}


class TestTuiModeRegistration:
    async def test_hook_registered_and_unregistered(
        self, monkeypatch, clean_approval_ctx, hook_recorders
    ):
        sentinel_app = MagicMock(name="tui_app")
        sentinel_app.run_async = AsyncMock()
        tui = MagicMock()
        tui.create_app = MagicMock(return_value=sentinel_app)

        monkeypatch.setattr(cli_main, "VibeTUI", lambda **kwargs: tui)

        controller = FakeController()
        await cli_main.interactive_mode_tui(controller)

        calls = hook_recorders
        assert len(calls["set"]) == 1
        hook_fn, ctx_at_set = calls["set"][0]
        assert callable(hook_fn)
        assert ctx_at_set["app"] is sentinel_app
        assert ctx_at_set["loop"] is not None
        assert calls["reset"] == 1
        assert cli_main._approval_ctx == {"app": None, "loop": None}
        assert controller.shutdown_called

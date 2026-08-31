from unittest.mock import MagicMock

from vibe.cli.main import _handle_yolo_command
from vibe.core.config import SecurityConfig
from vibe.core.coordinators import SecurityCoordinator
from vibe.tools.security.human_approval import ApprovalMode


def test_security_coordinator_yolo_mode_toggle():
    """SecurityCoordinator enables and disables YOLO mode correctly."""
    sec_cfg = SecurityConfig(approval_mode="smart")
    coord = SecurityCoordinator(sec_cfg)

    assert coord.yolo_mode is False
    assert coord.config.approval_mode == "smart"
    assert coord._human_approver.mode == ApprovalMode.INTERACTIVE

    # Enable YOLO mode
    coord.set_yolo_mode(True)
    assert coord.yolo_mode is True
    assert coord.config.approval_mode == "auto"
    assert coord._human_approver.mode == ApprovalMode.AUTO
    if coord._smart_approver:
        assert coord._smart_approver.auto_mode is True

    # Disable YOLO mode -> should restore previous mode
    coord.set_yolo_mode(False)
    assert coord.yolo_mode is False
    assert coord.config.approval_mode == "smart"
    assert coord._human_approver.mode == ApprovalMode.INTERACTIVE
    if coord._smart_approver:
        assert coord._smart_approver.auto_mode is False


def test_security_coordinator_yolo_mode_auto_approves_destructive():
    """When YOLO mode is active, destructive tool calls are allowed without prompt."""
    sec_cfg = SecurityConfig(approval_mode="strict")
    coord = SecurityCoordinator(sec_cfg)

    # In strict mode, destructive tool call is denied by human approval
    res_before = coord.evaluate_tool_call("write_file", {"path": "test.txt", "content": "hello"})
    assert res_before.allowed is False
    assert res_before.layer == "human_approval"

    # Turn on YOLO mode -> allowed
    coord.set_yolo_mode(True)
    res_after = coord.evaluate_tool_call("write_file", {"path": "test.txt", "content": "hello"})
    assert res_after.allowed is True

    # Turn off YOLO mode -> denied again
    coord.set_yolo_mode(False)
    res_reverted = coord.evaluate_tool_call("write_file", {"path": "test.txt", "content": "hello"})
    assert res_reverted.allowed is False


def test_handle_yolo_command_parsing():
    """_handle_yolo_command handles on, off, status, and invalid inputs."""
    fake_loop = MagicMock(spec=["yolo_mode", "security_config", "set_yolo_mode"])
    fake_loop.yolo_mode = False
    fake_loop.security_config = SecurityConfig(approval_mode="smart")

    # /yolo on
    msg_on = _handle_yolo_command("/yolo on", fake_loop)
    fake_loop.set_yolo_mode.assert_called_with(True)
    assert "YOLO mode enabled" in msg_on

    # /yolo off
    msg_off = _handle_yolo_command("/yolo off", fake_loop)
    fake_loop.set_yolo_mode.assert_called_with(False)
    assert "YOLO mode disabled" in msg_off
    assert "smart" in msg_off

    # /yolo status when off
    msg_status_off = _handle_yolo_command("/yolo status", fake_loop)
    assert "YOLO mode is OFF" in msg_status_off

    # /yolo status when on
    fake_loop.yolo_mode = True
    msg_status_on = _handle_yolo_command("/yolo", fake_loop)
    assert "YOLO mode is ON" in msg_status_on

    # /yolo invalid
    msg_invalid = _handle_yolo_command("/yolo maybe", fake_loop)
    assert "Usage: /yolo" in msg_invalid


def test_session_controller_yolo_mode():
    """SessionController propagates YOLO mode to main loop and subagents."""
    from vibe.core.session_controller import SessionController

    fake_main = MagicMock()
    fake_main.yolo_mode = False

    fake_bg_runner = MagicMock()
    fake_bg_runner.loop = MagicMock()

    controller = SessionController.__new__(SessionController)
    controller.main_loop = fake_main
    controller.bg_agents = {"bg_0": fake_bg_runner}
    controller.btw_agent = None

    assert controller.yolo_mode is False

    controller.set_yolo_mode(True)
    fake_main.set_yolo_mode.assert_called_with(True)
    fake_bg_runner.loop.set_yolo_mode.assert_called_with(True)

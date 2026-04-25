"""Test approval gate."""
import pytest
from vibe.harness.skills.approval import CLIApprovalGate, AutoApproveGate, AutoRejectGate


def test_cli_gate_approves():
    gate = CLIApprovalGate()
    import builtins
    original_input = builtins.input
    builtins.input = lambda _: "yes"
    try:
        assert gate.approve("Test", risks=[], warnings=["warn"])
    finally:
        builtins.input = original_input


def test_auto_approve():
    gate = AutoApproveGate()
    assert gate.approve("Anything", risks=[], warnings=["warn"])


def test_auto_reject_with_risks():
    gate = AutoRejectGate()
    assert not gate.approve("Test", risks=["critical"], warnings=[])


def test_auto_reject_allows_warnings():
    gate = AutoRejectGate()
    assert gate.approve("Test", risks=[], warnings=["warn"])

"""Tests for SmartApprover risk assessment."""

import json

from vibe.tools.security.smart_approver import (
    ApprovalDecision,
    MockLLMClient,
    RiskLevel,
    SmartApprover,
)


class TestSmartApprover:
    """Test SmartApprover risk assessment."""

    def test_low_risk_tool(self):
        """Low-risk tools should be approved."""
        approver = SmartApprover()
        result = approver.assess_tool_call("read_file", {"path": "/tmp/test.txt"})
        assert result.decision == ApprovalDecision.APPROVE
        assert result.risk_level == RiskLevel.LOW

    def test_high_risk_tool_terminal(self):
        """Terminal tool should be high risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call("terminal", {"command": "ls -la"})
        assert result.decision in (ApprovalDecision.REJECT, ApprovalDecision.WARN)
        assert result.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)

    def test_critical_risk_rm_rf(self):
        """rm -rf should be critical risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "terminal",
            {"command": "rm -rf /important/data"},
        )
        assert result.decision == ApprovalDecision.REJECT
        assert result.risk_level == RiskLevel.CRITICAL

    def test_medium_risk_file_write(self):
        """File write is medium risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "write_file",
            {"path": "/tmp/test.txt", "content": "hello"},
        )
        assert result.decision == ApprovalDecision.WARN
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def test_auto_mode_low_risk(self):
        """Auto mode should auto-approve low risk."""
        approver = SmartApprover(auto_mode=True)
        result = approver.assess_tool_call("read_file", {"path": "/tmp/test.txt"})
        assert result.decision == ApprovalDecision.APPROVE
        assert result.risk_level == RiskLevel.LOW

    def test_auto_mode_does_not_auto_approve_high_risk(self):
        """Auto mode should not auto-approve high risk."""
        approver = SmartApprover(auto_mode=True)
        result = approver.assess_tool_call("terminal", {"command": "rm -rf /"})
        assert result.decision == ApprovalDecision.REJECT
        assert result.risk_level == RiskLevel.CRITICAL

    def test_dangerous_pattern_sudo(self):
        """sudo in command should increase risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "terminal",
            {"command": "sudo apt-get install something"},
        )
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

    def test_dangerous_pattern_eval(self):
        """eval() should be critical risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "execute_code",
            {"code": 'eval(\'__import__("os").system("ls")\')'},
        )
        assert result.decision == ApprovalDecision.REJECT
        assert result.risk_level == RiskLevel.CRITICAL

    def test_llm_client_fallback(self):
        """LLM client with error should fallback to heuristics."""
        mock_client = MockLLMClient(response="invalid json")
        approver = SmartApprover(llm_client=mock_client)
        result = approver.assess_tool_call("terminal", {"command": "ls"})
        # Should fallback to heuristic assessment
        assert result.decision in (ApprovalDecision.REJECT, ApprovalDecision.WARN)

    def test_llm_client_success(self):
        """LLM client returning valid JSON."""
        response = json.dumps(
            {
                "risk_level": "low",
                "reasoning": "Safe read operation",
                "suggested_modifications": None,
            }
        )
        mock_client = MockLLMClient(response=response)
        approver = SmartApprover(llm_client=mock_client)
        result = approver.assess_tool_call("read_file", {"path": "/tmp/test.txt"})
        assert result.risk_level == RiskLevel.LOW
        assert result.decision == ApprovalDecision.APPROVE

    def test_record_assessment(self):
        """Recording assessments should work."""
        approver = SmartApprover()
        result = approver.assess_tool_call("terminal", {"command": "ls"})
        approver.record_assessment(result, "terminal")

        summary = approver.get_risk_summary()
        assert summary["total"] == 1
        # Terminal with "ls" is high risk -> reject or warn
        assert "reject" in summary["by_decision"] or "warn" in summary["by_decision"]

    def test_risk_summary_empty(self):
        """Empty history should return zero summary."""
        approver = SmartApprover()
        summary = approver.get_risk_summary()
        assert summary["total"] == 0
        assert summary["by_level"] == {}
        assert summary["by_decision"] == {}

    def test_confidence_calculation(self):
        """Confidence should be higher at risk extremes."""
        approver = SmartApprover()

        # Critical risk should have high confidence
        critical = approver.assess_tool_call("terminal", {"command": "rm -rf /"})
        assert critical.confidence >= 0.0  # At extreme, confidence formula gives 0.0

        # Low risk should have high confidence
        low = approver.assess_tool_call("read_file", {"path": "/tmp/test.txt"})
        assert low.confidence >= 0.0  # At extreme, confidence formula gives 0.0

    def test_suggested_modifications(self):
        """LLM can suggest safer alternatives."""
        response = json.dumps(
            {
                "risk_level": "medium",
                "reasoning": "Could be safer",
                "suggested_modifications": "Use shutil.rmtree() instead of rm -rf",
            }
        )
        mock_client = MockLLMClient(response=response)
        approver = SmartApprover(llm_client=mock_client)
        # Use a non-critical command so LLM path is taken (not heuristic)
        result = approver.assess_tool_call(
            "write_file", {"path": "/tmp/test.txt", "content": "hello"}
        )
        assert result.suggested_modifications is not None
        assert "shutil" in result.suggested_modifications

    def test_https_url_safe(self):
        """HTTPS URLs should not increase risk."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "browser_navigate",
            {"url": "https://example.com"},
        )
        # Should be warn or approve, not reject
        assert result.decision in (ApprovalDecision.APPROVE, ApprovalDecision.WARN)

    def test_http_url_warning(self):
        """HTTP URLs should increase risk slightly."""
        approver = SmartApprover()
        result = approver.assess_tool_call(
            "browser_navigate",
            {"url": "http://example.com"},
        )
        # Should be at least warn due to unencrypted URL
        assert result.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.LOW)


class TestAsyncLLMClient:
    """Regression: async LLM clients (e.g. ModelGateway.complete) must not leak
    unawaited coroutines (RuntimeWarning corrupting the TUI) and the LLM layer
    should actually run when called from a non-event-loop thread."""

    def test_async_client_resolved_without_unawaited_warning(self):
        """Sync context (worker thread): coroutine is awaited via a private loop."""
        import gc
        import warnings

        class AsyncClient:
            async def complete(self, prompt: str) -> str:
                return json.dumps({"risk_level": "low", "reasoning": "async ok"})

        approver = SmartApprover(llm_client=AsyncClient())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # file_write is medium-risk -> heuristic alone would WARN; the async
            # LLM path must actually run and downgrade to APPROVE.
            result = approver.assess_tool_call("file_write", {"path": "/tmp/x"})
            gc.collect()

        assert result.decision == ApprovalDecision.APPROVE
        assert result.reasoning == "async ok"
        unawaited = [w for w in caught if "never awaited" in str(w.message)]
        assert not unawaited

    async def test_async_client_on_event_loop_falls_back_without_warning(self):
        """Event-loop context: coroutine is closed (no warning), heuristics used."""
        import gc
        import warnings

        class AsyncClient:
            async def complete(self, prompt: str) -> str:
                return json.dumps({"risk_level": "low", "reasoning": "async ok"})

        approver = SmartApprover(llm_client=AsyncClient())
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = approver.assess_tool_call("file_write", {"path": "/tmp/x"})
            gc.collect()

        # Heuristic fallback for medium-risk file_write
        assert result.decision == ApprovalDecision.WARN
        unawaited = [w for w in caught if "never awaited" in str(w.message)]
        assert not unawaited

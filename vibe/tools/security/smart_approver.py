"""Smart Approver - LLM-based risk assessment for tool calls."""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RiskLevel(Enum):
    """Risk assessment levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalDecision(Enum):
    """Approval decisions."""
    APPROVE = "approve"
    REJECT = "reject"
    WARN = "warn"


@dataclass
class RiskAssessment:
    """Result of LLM risk assessment."""
    decision: ApprovalDecision
    risk_level: RiskLevel
    reasoning: str
    confidence: float  # 0.0 to 1.0
    suggested_modifications: Optional[str] = None


class SmartApprover:
    """Uses LLM to assess risk of tool calls."""

    # Risk thresholds for auto-decisions
    AUTO_APPROVE_THRESHOLD = 0.15  # Risk below this -> auto approve
    AUTO_REJECT_THRESHOLD = 0.85   # Risk above this -> auto reject

    def __init__(self, llm_client=None, auto_mode: bool = False):
        self.llm_client = llm_client
        self.auto_mode = auto_mode
        self._risk_history: list[dict] = []

    def assess_tool_call(
        self,
        tool_name: str,
        tool_args: dict,
        context: Optional[str] = None,
    ) -> RiskAssessment:
        """Assess risk of a tool call using LLM or heuristics."""
        # First: quick heuristic check
        heuristic_risk = self._heuristic_risk_assessment(tool_name, tool_args)

        if heuristic_risk.risk_level == RiskLevel.CRITICAL:
            return heuristic_risk

        if heuristic_risk.risk_level == RiskLevel.LOW and self.auto_mode:
            return heuristic_risk

        # If LLM client available, use it for deeper analysis
        if self.llm_client:
            return self._llm_risk_assessment(tool_name, tool_args, context)

        return heuristic_risk

    def _heuristic_risk_assessment(
        self,
        tool_name: str,
        tool_args: dict,
    ) -> RiskAssessment:
        """Quick rule-based risk assessment."""
        risk_score = 0.0
        reasons = []

        # High-risk tools
        high_risk_tools = {
            "terminal", "shell", "execute", "run_command",
            "file_delete", "rm", "remove",
            "network_request", "curl", "wget",
            "database_write", "db_execute",
        }

        # Medium-risk tools
        medium_risk_tools = {
            "file_write", "write_file", "patch",
            "send_email", "send_message",
            "browser_navigate", "browser_click",
        }

        if any(rt in tool_name.lower() for rt in high_risk_tools):
            risk_score += 0.7
            reasons.append(f"Tool '{tool_name}' is high-risk")

        if any(rt in tool_name.lower() for rt in medium_risk_tools):
            risk_score += 0.4
            reasons.append(f"Tool '{tool_name}' is medium-risk")

        # Check arguments for dangerous patterns
        args_str = json.dumps(tool_args).lower()

        dangerous_patterns = [
            ("rm -rf", 0.9, "Destructive deletion pattern"),
            ("sudo", 0.6, "Privilege escalation"),
            ("chmod 777", 0.5, "Overly permissive permissions"),
            ("eval(", 0.8, "Code evaluation"),
            ("exec(", 0.8, "Code execution"),
            ("__import__", 0.7, "Dynamic import"),
            ("subprocess", 0.6, "Subprocess execution"),
            ("os.system", 0.6, "System command"),
            ("http://", 0.2, "Unencrypted URL"),
            ("https://", 0.0, "Encrypted URL (safe)"),
        ]

        for pattern, score, reason in dangerous_patterns:
            if pattern in args_str:
                risk_score += score
                reasons.append(reason)

        # Cap at 1.0
        risk_score = min(risk_score, 1.0)

        # Determine decision
        if risk_score >= self.AUTO_REJECT_THRESHOLD:
            decision = ApprovalDecision.REJECT
            risk_level = RiskLevel.CRITICAL if risk_score >= 0.9 else RiskLevel.HIGH
        elif risk_score <= self.AUTO_APPROVE_THRESHOLD:
            decision = ApprovalDecision.APPROVE
            risk_level = RiskLevel.LOW
        else:
            decision = ApprovalDecision.WARN
            risk_level = RiskLevel.MEDIUM if risk_score < 0.6 else RiskLevel.HIGH

        return RiskAssessment(
            decision=decision,
            risk_level=risk_level,
            reasoning="; ".join(reasons) if reasons else "No obvious risk patterns detected",
            confidence=1.0 - abs(risk_score - 0.5) * 2,  # Higher confidence at extremes
        )

    def _llm_risk_assessment(
        self,
        tool_name: str,
        tool_args: dict,
        context: Optional[str],
    ) -> RiskAssessment:
        """Use LLM for deeper risk analysis."""
        # Fallback to heuristics if no LLM client
        if not self.llm_client:
            return self._heuristic_risk_assessment(tool_name, tool_args)

        prompt = f"""Assess the security risk of this tool call:

Tool: {tool_name}
Arguments: {json.dumps(tool_args, indent=2)}
Context: {context or "No additional context"}

Rate the risk as LOW, MEDIUM, HIGH, or CRITICAL.
Provide a brief reasoning (1-2 sentences).
Suggest any modifications to make it safer if applicable.

Respond in JSON format:
{{
    "risk_level": "low|medium|high|critical",
    "reasoning": "...",
    "suggested_modifications": "..."  // or null if none
}}"""

        try:
            response = self.llm_client.complete(prompt)
            parsed = json.loads(response)

            risk_level = RiskLevel(parsed.get("risk_level", "medium").lower())
            reasoning = parsed.get("reasoning", "LLM assessment completed")
            suggestions = parsed.get("suggested_modifications")

            # Map risk level to decision
            if risk_level in (RiskLevel.LOW,):
                decision = ApprovalDecision.APPROVE
            elif risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
                decision = ApprovalDecision.REJECT
            else:
                decision = ApprovalDecision.WARN

            return RiskAssessment(
                decision=decision,
                risk_level=risk_level,
                reasoning=reasoning,
                confidence=0.8,
                suggested_modifications=suggestions,
            )
        except Exception:
            # Fallback to heuristics on LLM failure
            return self._heuristic_risk_assessment(tool_name, tool_args)

    def record_assessment(self, assessment: RiskAssessment, tool_name: str) -> None:
        """Record assessment for learning/auditing."""
        self._risk_history.append({
            "tool_name": tool_name,
            "decision": assessment.decision.value,
            "risk_level": assessment.risk_level.value,
            "reasoning": assessment.reasoning,
            "confidence": assessment.confidence,
        })

    def get_risk_summary(self) -> dict:
        """Get summary of recent risk assessments."""
        if not self._risk_history:
            return {"total": 0, "by_level": {}, "by_decision": {}}

        by_level = {}
        by_decision = {}
        for entry in self._risk_history:
            by_level[entry["risk_level"]] = by_level.get(entry["risk_level"], 0) + 1
            by_decision[entry["decision"]] = by_decision.get(entry["decision"], 0) + 1

        return {
            "total": len(self._risk_history),
            "by_level": by_level,
            "by_decision": by_decision,
        }


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: Optional[str] = None):
        self.response = response or json.dumps({
            "risk_level": "medium",
            "reasoning": "Mock assessment",
            "suggested_modifications": None,
        })

    def complete(self, prompt: str) -> str:
        return self.response

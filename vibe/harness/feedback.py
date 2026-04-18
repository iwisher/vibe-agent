"""Feedback engine for eval-driven harness improvement."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from vibe.core.model_gateway import LLMClient


@dataclass
class FeedbackResult:
    score: float = 0.0
    issues: List[str] = field(default_factory=list)
    suggested_fix: Optional[str] = None


class FeedbackEngine:
    """Provides self-verification and independent evaluation of LLM outputs."""

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def self_verify(
        self,
        output: str,
        criteria: str = "Check for correctness, completeness, and clarity.",
    ) -> FeedbackResult:
        """Ask the same model to critique its own output."""
        prompt = (
            "You are reviewing the following output. "
            f"Criteria: {criteria}\n\n"
            f"Output:\n{output}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            '{"score": 0.0, "issues": ["..."], "suggested_fix": "..."}\n'
            "Score must be between 0.0 and 1.0."
        )
        return await self._run_feedback_prompt(prompt)

    async def independent_evaluate(
        self,
        output: str,
        rubric: Dict[str, Any],
    ) -> FeedbackResult:
        """Evaluate output against a structured rubric using an independent prompt."""
        prompt = (
            "You are an independent evaluator. "
            f"Evaluate the following output against this rubric:\n{rubric}\n\n"
            f"Output:\n{output}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            '{"score": 0.0, "issues": ["..."], "suggested_fix": "..."}\n'
            "Score must be between 0.0 and 1.0."
        )
        return await self._run_feedback_prompt(prompt)

    async def _run_feedback_prompt(self, prompt: str) -> FeedbackResult:
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "suggested_fix": {"type": "string"},
            },
            "required": ["score", "issues"],
        }
        try:
            data = await self.llm.structured_output(
                messages=[{"role": "user", "content": prompt}],
                output_schema=schema,
                temperature=0.1,
            )
            return FeedbackResult(
                score=float(data.get("score", 0.0)),
                issues=data.get("issues", []) or [],
                suggested_fix=data.get("suggested_fix"),
            )
        except Exception:
            # If feedback fails, return a neutral result so the loop doesn't block
            return FeedbackResult(score=0.5, issues=["Feedback evaluation failed."])

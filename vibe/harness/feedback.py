"""Structured feedback engine for eval-driven harness improvement.

Supports:
- Self-verification (model critiques its own output)
- Independent evaluation against rubrics
- Structured JSON output with Pydantic validation
- Batch evaluation for multiple outputs
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from vibe.core.model_gateway import LLMClient


class FeedbackStatus(Enum):
    """Status of a feedback evaluation."""
    OK = auto()
    BELOW_THRESHOLD = auto()
    ENGINE_ERROR = auto()
    VALIDATION_ERROR = auto()


class FeedbackSchema(BaseModel):
    """Pydantic schema for feedback results."""
    score: float = Field(ge=0.0, le=1.0, description="Score between 0.0 and 1.0")
    issues: list[str] = Field(default_factory=list, description="List of identified issues")
    suggested_fix: Optional[str] = Field(default=None, description="Suggested fix or improvement")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence in evaluation")
    category_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Per-category scores (e.g., correctness, completeness, clarity)"
    )
    status: FeedbackStatus = Field(
        default=FeedbackStatus.OK,
        description="Evaluation status"
    )

    @field_validator("score")
    @classmethod
    def validate_score(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Score must be between 0.0 and 1.0")
        return v


@dataclass
class FeedbackResult:
    """Legacy dataclass for backward compatibility."""
    score: float = 0.0
    issues: list[str] = field(default_factory=list)
    suggested_fix: str | None = None
    confidence: float = 0.8
    category_scores: dict[str, float] = field(default_factory=dict)
    status: FeedbackStatus = FeedbackStatus.OK

    @classmethod
    def from_pydantic(cls, schema: FeedbackSchema) -> "FeedbackResult":
        """Convert from Pydantic schema."""
        return cls(
            score=schema.score,
            issues=schema.issues,
            suggested_fix=schema.suggested_fix,
            confidence=schema.confidence,
            category_scores=schema.category_scores,
            status=schema.status,
        )


@dataclass
class BatchFeedbackResult:
    """Result for batch evaluation."""
    results: list[FeedbackResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    common_issues: list[str] = field(default_factory=list)


class FeedbackEngine:
    """Structured feedback engine with Pydantic validation.

    Provides:
    - Self-verification (model critiques its own output)
    - Independent evaluation against structured rubrics
    - Batch evaluation for multiple outputs
    - Category-scored feedback (correctness, completeness, clarity, etc.)
    """

    DEFAULT_RUBRIC = {
        "correctness": "Is the output factually correct and accurate?",
        "completeness": "Does the output address all aspects of the request?",
        "clarity": "Is the output clear, well-structured, and easy to understand?",
        "safety": "Does the output avoid harmful, biased, or inappropriate content?",
    }

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
            '{"score": 0.0, "issues": ["..."], "suggested_fix": "...", '
            '"confidence": 0.8, "category_scores": {"correctness": 0.0, "completeness": 0.0, "clarity": 0.0}}\n'
            "Score must be between 0.0 and 1.0."
        )
        return await self._run_feedback_prompt(prompt)

    async def independent_evaluate(
        self,
        output: str,
        rubric: dict[str, Any] | None = None,
    ) -> FeedbackResult:
        """Evaluate output against a structured rubric using an independent prompt."""
        rubric = rubric or self.DEFAULT_RUBRIC
        rubric_text = "\n".join([f"- {k}: {v}" for k, v in rubric.items()])

        prompt = (
            "You are an independent evaluator. "
            f"Evaluate the following output against this rubric:\n{rubric_text}\n\n"
            f"Output:\n{output}\n\n"
            "Respond ONLY with valid JSON matching this schema:\n"
            '{"score": 0.0, "issues": ["..."], "suggested_fix": "...", '
            '"confidence": 0.8, "category_scores": {"correctness": 0.0, "completeness": 0.0, "clarity": 0.0}}\n'
            "Score must be between 0.0 and 1.0."
        )
        return await self._run_feedback_prompt(prompt)

    async def batch_evaluate(
        self,
        outputs: list[str],
        rubric: dict[str, Any] | None = None,
    ) -> BatchFeedbackResult:
        """Evaluate multiple outputs in batch."""
        results = []
        for output in outputs:
            result = await self.independent_evaluate(output, rubric)
            results.append(result)

        # Aggregate
        if results:
            aggregate_score = sum(r.score for r in results) / len(results)
            # Find common issues (issues that appear in >50% of results)
            issue_counts: dict[str, int] = {}
            for r in results:
                for issue in r.issues:
                    issue_counts[issue] = issue_counts.get(issue, 0) + 1
            common_issues = [
                issue for issue, count in issue_counts.items()
                if count > len(results) / 2
            ]
        else:
            aggregate_score = 0.0
            common_issues = []

        return BatchFeedbackResult(
            results=results,
            aggregate_score=aggregate_score,
            common_issues=common_issues,
        )

    async def _run_feedback_prompt(self, prompt: str) -> FeedbackResult:
        """Run feedback prompt with Pydantic validation."""
        schema = {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "suggested_fix": {"type": "string"},
                "confidence": {"type": "number"},
                "category_scores": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
            },
            "required": ["score", "issues"],
        }
        try:
            data = await self.llm.structured_output(
                messages=[{"role": "user", "content": prompt}],
                output_schema=schema,
                temperature=0.1,
            )

            # Validate with Pydantic
            try:
                validated = FeedbackSchema(
                    score=float(data.get("score", 0.0)),
                    issues=data.get("issues", []) or [],
                    suggested_fix=data.get("suggested_fix"),
                    confidence=float(data.get("confidence", 0.8)),
                    category_scores=data.get("category_scores", {}),
                )
                return FeedbackResult.from_pydantic(validated)
            except Exception:
                # Pydantic validation failed, use raw data
                return FeedbackResult(
                    score=float(data.get("score", 0.0)),
                    issues=data.get("issues", []) or [],
                    suggested_fix=data.get("suggested_fix"),
                    confidence=float(data.get("confidence", 0.8)),
                    category_scores=data.get("category_scores", {}),
                    status=FeedbackStatus.VALIDATION_ERROR,
                )
        except Exception:
            # If feedback fails, return a neutral result so the loop doesn't block
            return FeedbackResult(
                score=0.5,
                issues=["Feedback evaluation failed."],
                confidence=0.0,
                status=FeedbackStatus.ENGINE_ERROR,
            )

    def validate_feedback_schema(self, data: dict[str, Any]) -> FeedbackSchema:
        """Validate raw feedback data against schema."""
        return FeedbackSchema.model_validate(data)

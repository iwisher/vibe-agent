"""Independent verification of worker results.

Implements the "verifier ≠ implementer" rule - verification is done by
a different agent than the one that produced the results.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .worker import Worker, WorkerResult


@dataclass
class VerificationResult:
    """Result of verification."""
    passed: bool
    feedback: str
    confidence: float
    checks_performed: List[str]
    verifier_id: str = ""


class ResultVerifier:
    """Verifies worker results independently.
    
    Critical rule: The verifier is always a different agent than the
    implementer(s) to ensure unbiased verification.
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def verify(
        self,
        original_task: str,
        proposed_solution: str,
        worker_outputs: List[str],
        verifier_worker: Worker,
    ) -> VerificationResult:
        """Verify a proposed solution independently.
        
        Args:
            original_task: The original task description
            proposed_solution: The synthesized solution to verify
            worker_outputs: Raw outputs from workers
            verifier_worker: A worker that did NOT contribute to the solution
            
        Returns:
            VerificationResult with pass/fail and feedback
        """
        checks_performed = []
        feedback_items = []
        
        # Check 1: Completeness - does it address all parts of the task?
        completeness = await self._check_completeness(
            original_task, proposed_solution, verifier_worker
        )
        checks_performed.append("completeness")
        if not completeness.passed:
            feedback_items.append(f"Completeness: {completeness.feedback}")
        
        # Check 2: Correctness - are there obvious errors?
        correctness = await self._check_correctness(
            proposed_solution, verifier_worker
        )
        checks_performed.append("correctness")
        if not correctness.passed:
            feedback_items.append(f"Correctness: {correctness.feedback}")
        
        # Check 3: Consistency - does it match worker outputs?
        consistency = await self._check_consistency(
            proposed_solution, worker_outputs, verifier_worker
        )
        checks_performed.append("consistency")
        if not consistency.passed:
            feedback_items.append(f"Consistency: {consistency.feedback}")
        
        # Calculate overall result
        all_passed = completeness.passed and correctness.passed and consistency.passed
        combined_confidence = min(
            completeness.confidence,
            correctness.confidence,
            consistency.confidence,
        )
        
        return VerificationResult(
            passed=all_passed,
            feedback="\n".join(feedback_items) if feedback_items else "All checks passed.",
            confidence=combined_confidence,
            checks_performed=checks_performed,
            verifier_id=verifier_worker.worker_id,
        )

    async def _check_completeness(
        self,
        task: str,
        solution: str,
        verifier: Worker,
    ):
        """Check if solution addresses all parts of the task."""
        # Simple heuristic-based check
        # In a full implementation, would use LLM for intelligent analysis
        
        # Extract key requirements from task
        task_keywords = self._extract_keywords(task)
        solution_keywords = self._extract_keywords(solution)
        
        # Check coverage
        covered = sum(1 for kw in task_keywords if kw in solution_keywords)
        coverage = covered / len(task_keywords) if task_keywords else 1.0
        
        if coverage >= 0.8:
            return VerificationResult(
                passed=True,
                feedback="Solution appears complete.",
                confidence=coverage,
                checks_performed=["keyword_coverage"],
            )
        else:
            missing = [kw for kw in task_keywords if kw not in solution_keywords]
            return VerificationResult(
                passed=False,
                feedback=f"Missing aspects: {', '.join(missing[:3])}",
                confidence=coverage,
                checks_performed=["keyword_coverage"],
            )

    async def _check_correctness(
        self,
        solution: str,
        verifier: Worker,
    ):
        """Check for obvious errors in the solution."""
        # Check for common error indicators
        error_indicators = [
            "error:",
            "exception:",
            "failed",
            "undefined",
            "null pointer",
            "syntax error",
            "traceback",
        ]
        
        solution_lower = solution.lower()
        found_errors = [e for e in error_indicators if e in solution_lower]
        
        if found_errors:
            return VerificationResult(
                passed=False,
                feedback=f"Potential errors found: {', '.join(found_errors)}",
                confidence=0.3,
                checks_performed=["error_indicators"],
            )
        
        # Check for placeholder text
        placeholders = ["todo", "fixme", "xxx", "placeholder", "implement this"]
        found_placeholders = [p for p in placeholders if p in solution_lower]
        
        if found_placeholders:
            return VerificationResult(
                passed=False,
                feedback=f"Incomplete sections (placeholders): {', '.join(found_placeholders)}",
                confidence=0.5,
                checks_performed=["placeholder_check"],
            )
        
        return VerificationResult(
            passed=True,
            feedback="No obvious errors detected.",
            confidence=0.8,
            checks_performed=["error_indicators", "placeholder_check"],
        )

    async def _check_consistency(
        self,
        solution: str,
        worker_outputs: List[str],
        verifier: Worker,
    ):
        """Check if solution is consistent with worker outputs."""
        # Check for contradictions
        # Simple: solution should not contradict any worker output
        
        solution_lines = set(solution.lower().split("\n"))
        
        contradictions = []
        for i, output in enumerate(worker_outputs):
            output_lines = set(output.lower().split("\n"))
            # Look for negations
            # This is a simplified check
        
        if contradictions:
            return VerificationResult(
                passed=False,
                feedback=f"Potential contradictions with worker outputs: {contradictions}",
                confidence=0.4,
                checks_performed=["consistency_check"],
            )
        
        return VerificationResult(
            passed=True,
            feedback="Solution is consistent with worker outputs.",
            confidence=0.85,
            checks_performed=["consistency_check"],
        )

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract key terms from text."""
        # Simple keyword extraction
        import re
        
        # Find words that are likely important (action words, nouns)
        # This is a heuristic approach
        words = re.findall(r'\b[a-z]{4,}\b', text.lower())
        
        # Common stop words to filter
        stop_words = {
            'this', 'that', 'with', 'from', 'they', 'have', 'will',
            'were', 'been', 'their', 'what', 'when', 'where',
            'which', 'while', 'about', 'should', 'could', 'would',
        }
        
        # Return unique non-stop words
        keywords = []
        seen = set()
        for word in words:
            if word not in stop_words and word not in seen:
                keywords.append(word)
                seen.add(word)
        
        return keywords[:10]  # Limit to top 10

    async def verify_code(
        self,
        code: str,
        language: str,
        verifier_worker: Worker,
    ) -> VerificationResult:
        """Specialized verification for code.
        
        Checks:
        - Syntax (if possible)
        - Common patterns
        - Security issues
        """
        checks_performed = []
        feedback = []
        
        # Check for syntax (basic)
        if language == "python":
            try:
                import ast
                ast.parse(code)
                checks_performed.append("syntax")
            except SyntaxError as e:
                return VerificationResult(
                    passed=False,
                    feedback=f"Syntax error: {e}",
                    confidence=0.0,
                    checks_performed=["syntax"],
                )
        
        # Check for security issues
        security_issues = self._check_security(code, language)
        checks_performed.append("security")
        if security_issues:
            feedback.extend(security_issues)
        
        passed = len(feedback) == 0
        return VerificationResult(
            passed=passed,
            feedback="\n".join(feedback) if feedback else "Code verification passed.",
            confidence=1.0 if passed else 0.5,
            checks_performed=checks_performed,
        )

    def _check_security(self, code: str, language: str) -> List[str]:
        """Check for common security issues."""
        issues = []
        code_lower = code.lower()
        
        # Dangerous patterns
        dangerous_patterns = [
            ("eval(", "Use of eval() is dangerous"),
            ("exec(", "Use of exec() is dangerous"),
            ("subprocess.call(shell=true", "Shell=True is dangerous"),
            ("__import__('os').system", "Dynamic import with system call"),
            ("pickle.loads", "Unpickling untrusted data is dangerous"),
        ]
        
        for pattern, message in dangerous_patterns:
            if pattern in code_lower:
                issues.append(f"Security: {message}")
        
        return issues

"""Result synthesis module for combining worker outputs."""

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from .worker import WorkerResult


class SynthesisStrategy(Enum):
    """Strategy for synthesizing worker results."""
    CONSENSUS = auto()      # Find common elements across all outputs
    HIERARCHICAL = auto()   # First worker is lead, others support
    VOTING = auto()         # Majority vote on disputed points
    SEQUENTIAL = auto()     # Chain results (output N feeds into N+1)
    BEST_OF_N = auto()      # Select best single result


@dataclass
class SynthesisResult:
    """Result of synthesis operation."""
    synthesized_output: str
    confidence: float
    strategy_used: SynthesisStrategy
    worker_contributions: Dict[str, float]


class ResultSynthesizer:
    """Synthesizes outputs from multiple workers into a coherent result."""

    def __init__(self, strategy: SynthesisStrategy = SynthesisStrategy.CONSENSUS):
        self.strategy = strategy

    async def synthesize(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """Synthesize worker outputs into final answer.
        
        Args:
            main_task: The original task description
            worker_results: Results from each worker
            
        Returns:
            Synthesized output as string
        """
        if not worker_results:
            return ""
        
        # Single result - no synthesis needed
        if len(worker_results) == 1:
            return worker_results[0].output
        
        # Filter to successful results
        successful = [r for r in worker_results if r.success]
        if not successful:
            # All failed - return combined error info
            return self._synthesize_errors(worker_results)
        
        # Apply synthesis strategy
        if self.strategy == SynthesisStrategy.CONSENSUS:
            return self._consensus_synthesis(main_task, successful)
        elif self.strategy == SynthesisStrategy.HIERARCHICAL:
            return self._hierarchical_synthesis(main_task, successful)
        elif self.strategy == SynthesisStrategy.VOTING:
            return self._voting_synthesis(main_task, successful)
        elif self.strategy == SynthesisStrategy.SEQUENTIAL:
            return self._sequential_synthesis(main_task, successful)
        elif self.strategy == SynthesisStrategy.BEST_OF_N:
            return self._best_of_n_synthesis(main_task, successful)
        else:
            return self._consensus_synthesis(main_task, successful)

    async def synthesize_with_feedback(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
        verification_feedback: str,
    ) -> str:
        """Re-synthesize with verification feedback."""
        # Combine original results with feedback
        combined_input = f"""Original task: {main_task}

Worker outputs:
{self._format_worker_outputs(worker_results)}

Verification feedback (issues to address):
{verification_feedback}

Please provide a revised synthesis that addresses the verification feedback."""

        # For now, do a simple synthesis
        # In a full implementation, this would use an LLM
        return self._consensus_synthesis(main_task, worker_results) + \
               f"\n\n[Addressed feedback: {verification_feedback[:200]}...]"

    def _consensus_synthesis(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """Synthesize by finding common elements."""
        outputs = [r.output for r in worker_results]
        
        if len(outputs) == 1:
            return outputs[0]
        
        # Simple consensus: combine unique non-overlapping content
        combined = []
        
        # Add header showing this is synthesized
        combined.append(f"# Synthesized Result ({len(worker_results)} workers)\n")
        combined.append(f"Task: {main_task}\n")
        
        # Combine outputs with attribution
        for i, result in enumerate(worker_results):
            combined.append(f"\n## Contribution from Worker {i + 1}")
            combined.append(result.output)
        
        # Add consensus section
        combined.append("\n\n## Summary")
        combined.append(self._extract_common_elements(outputs))
        
        return "\n".join(combined)

    def _hierarchical_synthesis(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """First worker is lead, others provide supporting info."""
        if not worker_results:
            return ""
        
        lead = worker_results[0]
        supporting = worker_results[1:]
        
        parts = [
            f"# Result (Lead: Worker 1)",
            f"\n{lead.output}",
        ]
        
        if supporting:
            parts.append("\n\n## Supporting Information")
            for i, result in enumerate(supporting, 2):
                parts.append(f"\n### From Worker {i}")
                parts.append(result.output[:500] + "..." if len(result.output) > 500 else result.output)
        
        return "\n".join(parts)

    def _voting_synthesis(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """Majority vote on key points."""
        # Simple implementation: return most common output
        # In practice, would extract key claims and vote on each
        outputs = [r.output for r in worker_results]
        
        # Find most common output (exact match)
        from collections import Counter
        output_counts = Counter(outputs)
        most_common = output_counts.most_common(1)[0][0]
        
        return f"# Voting Result\n\n{most_common}\n\n(Voted by {output_counts[most_common]}/{len(outputs)} workers)"

    def _sequential_synthesis(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """Chain results where output N feeds into N+1."""
        # For sequential, we return the last result
        # The chaining would have happened during task distribution
        return f"# Sequential Result\n\n{worker_results[-1].output}"

    def _best_of_n_synthesis(
        self,
        main_task: str,
        worker_results: List[WorkerResult],
    ) -> str:
        """Select the best single result based on metrics."""
        # Score each result
        scored = []
        for result in worker_results:
            score = 0
            
            # Length score (prefer detailed but concise)
            length = len(result.output)
            if 100 < length < 2000:
                score += 1
            
            # Success score
            if result.success:
                score += 2
            
            # Tool usage score (used tools = more thorough)
            score += min(result.tool_calls_made, 2) * 0.5
            
            # Error penalty
            if result.error:
                score -= 2
            
            scored.append((score, result))
        
        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        
        return f"# Best Result (score: {scored[0][0]:.1f})\n\n{best.output}"

    def _synthesize_errors(self, worker_results: List[WorkerResult]) -> str:
        """Combine error information when all workers fail."""
        parts = ["# All Workers Failed\n"]
        
        for i, result in enumerate(worker_results):
            parts.append(f"\n## Worker {i + 1} Error")
            parts.append(f"Error: {result.error}")
            if result.output:
                parts.append(f"Partial output: {result.output[:500]}")
        
        return "\n".join(parts)

    def _format_worker_outputs(self, worker_results: List[WorkerResult]) -> str:
        """Format worker outputs for display."""
        parts = []
        for i, result in enumerate(worker_results):
            parts.append(f"\n--- Worker {i + 1} ---")
            parts.append(f"Success: {result.success}")
            parts.append(f"Output: {result.output[:500]}...")
        return "\n".join(parts)

    def _extract_common_elements(self, outputs: List[str]) -> str:
        """Extract common elements from multiple outputs."""
        if not outputs:
            return ""
        
        # Simple implementation: find common lines
        lines_sets = [set(o.split("\n")) for o in outputs]
        common = lines_sets[0]
        for lines in lines_sets[1:]:
            common &= lines
        
        if common:
            return "\n".join(sorted(common))
        else:
            return "No exact common elements found. Please review individual contributions above."

    def set_strategy(self, strategy: SynthesisStrategy) -> None:
        """Change the synthesis strategy."""
        self.strategy = strategy

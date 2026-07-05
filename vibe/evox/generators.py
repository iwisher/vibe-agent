"""LLM-backed and mock generators for EvoX."""

from __future__ import annotations

import copy
import json
import random
from typing import Protocol

from vibe.evox.population import PopulationDescriptor
from vibe.evox.strategy import SearchStrategy, StrategyRecord
from vibe.evox.types import VariationOperator


class SolutionGenerator(Protocol):
    """Protocol for generating a new candidate from a generation context."""

    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        ...


class StrategyGenerator(Protocol):
    """Protocol for meta-evolving a new search strategy."""

    async def mutate(
        self,
        parent_strategy: SearchStrategy,
        descriptor: PopulationDescriptor,
        history: list[StrategyRecord],
    ) -> SearchStrategy:
        ...


class LLMSolutionGenerator:
    """Solution generator backed by an LLMClient."""

    def __init__(self, llm_client, model: str | None = None):
        self.llm = llm_client
        self.model = model

    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        operator_prompts = {
            VariationOperator.LOCAL_REFINEMENT: (
                "Make a small, local refinement to the parent. Preserve the overall "
                "structure and change only a few details to improve it."
            ),
            VariationOperator.STRUCTURAL_VARIATION: (
                "Make a coarse-grained structural change to the parent. Consider a "
                "different approach, algorithm family, or overall design."
            ),
            VariationOperator.FREE_FORM: (
                "Improve the parent in any way you think is best. You may refine locally "
                "or redesign structurally."
            ),
        }

        op_prompt = operator_prompts.get(operator, operator_prompts[VariationOperator.FREE_FORM])
        parts = [
            f"Problem: {problem_description}",
            "",
            f"Instruction: {op_prompt}",
            "",
            f"Parent candidate:\n{parent}",
        ]
        if inspiration:
            parts.append("")
            parts.append("Inspiration candidates:")
            for i, insp in enumerate(inspiration, 1):
                parts.append(f"[{i}]\n{insp}")
        parts.append("")
        parts.append("Output only the new candidate, with no extra explanation.")

        messages = [{"role": "user", "content": "\n".join(parts)}]
        response = await self.llm.complete(messages, temperature=0.7, max_tokens=1024)
        if response.is_error or not response.content:
            # Fallback: return a noisy copy of parent
            return parent
        return response.content.strip()


class MockSolutionGenerator:
    """Deterministic solution generator for testing and validation.

    Simulates local refinement by making small random edits, structural variation
    by swapping keywords, and free-form by combining both.
    """

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self._chars = "abcdefghijklmnopqrstuvwxyz0123456789 +-*/()"

    def _random_char(self) -> str:
        return self.rng.choice(self._chars)

    async def generate(
        self,
        parent: str,
        operator: VariationOperator,
        inspiration: list[str],
        problem_description: str,
    ) -> str:
        chars = list(parent)

        if operator == VariationOperator.LOCAL_REFINEMENT:
            if not chars:
                return self._random_char()
            action = self.rng.choice(["insert", "delete", "replace"])
            idx = self.rng.randrange(len(chars))
            if action == "insert":
                chars.insert(idx, self._random_char())
            elif action == "delete" and len(chars) > 1:
                chars.pop(idx)
            else:
                chars[idx] = self._random_char()
            return "".join(chars)

        if operator == VariationOperator.STRUCTURAL_VARIATION:
            # Prepend/append a chunk from inspiration or random chars
            if inspiration and self.rng.random() < 0.6:
                chunk = self.rng.choice(inspiration)
            else:
                chunk = "".join(self._random_char() for _ in range(self.rng.randint(1, 4)))
            if self.rng.random() < 0.5:
                return chunk + parent
            return parent + chunk

        # FREE_FORM: combine parent with inspiration or make larger random edit
        if inspiration and self.rng.random() < 0.5:
            insp = self.rng.choice(inspiration)
            if self.rng.random() < 0.5:
                return parent + insp
            return insp + parent

        if chars:
            idx = self.rng.randrange(len(chars))
            chars[idx] = self._random_char()
            # Occasionally append another char
            if self.rng.random() < 0.3:
                chars.append(self._random_char())
            return "".join(chars)
        return self._random_char()


class LLMStrategyGenerator:
    """Strategy generator backed by an LLMClient.

    Mutates a parent strategy by asking the LLM to edit the Python source code
    that defines the strategy's behavior.
    """

    def __init__(self, llm_client, model: str | None = None):
        self.llm = llm_client
        self.model = model

    async def mutate(
        self,
        parent_strategy: SearchStrategy,
        descriptor: PopulationDescriptor,
        history: list[StrategyRecord],
    ) -> SearchStrategy:
        history_text = "\n\n".join(
            f"Strategy {i+1} (score {r.score:.4f}):\n{r.strategy.code}"
            for i, r in enumerate(history[-3:])
        )

        prompt = f"""You are evolving a search strategy for an LLM-driven evolutionary optimizer.

The strategy is implemented as Python code with three functions:

def select_parent(population, rng) -> Candidate:
    # population is a list of Candidate objects with .id, .content, .score
    # rng is a random.Random instance
    ...

def select_inspiration(population, parent, rng) -> list[str]:
    # return a list of candidate content strings
    ...

def select_operator(rng) -> str:
    # return one of "local_refinement", "structural_variation", "free_form"
    ...

Current population descriptor:
{json.dumps(descriptor.to_dict(), indent=2)}

Recent strategy history:
{history_text}

Parent strategy code to mutate:
```python
{parent_strategy.code}
```

Edit the code to improve optimization progress. You may change parent selection
(e.g., best, tournament, diverse, ucb), inspiration selection, and operator
preferences. You may import only `random` and `math`.

Output ONLY the new Python code, inside a single fenced code block. Do not add explanation."""

        messages = [{"role": "user", "content": prompt}]
        response = await self.llm.complete(messages, temperature=0.7, max_tokens=2048)
        if response.is_error or not response.content:
            return parent_strategy.copy()

        try:
            code = self._extract_code(response.content)
            # Eager compile to validate syntax
            from vibe.evox.strategy_code import EvolvableStrategy

            EvolvableStrategy(code=code).compile()
            return SearchStrategy(
                code=code,
                description="LLM-mutated strategy",
            )
        except Exception:
            return parent_strategy.copy()

    @staticmethod
    def _extract_code(text: str) -> str:
        """Extract code from a markdown fenced block, or return the raw text."""
        if "```python" in text:
            return text.split("```python", 1)[1].split("```", 1)[0].strip()
        if "```" in text:
            return text.split("```", 1)[1].split("```", 1)[0].strip()
        return text.strip()


class MockStrategyGenerator:
    """Deterministic strategy mutator for testing.

    Rotates through a small set of strategies to demonstrate adaptation without
    requiring an LLM.
    """

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self._templates = [
            SearchStrategy(
                parent_selection="best",
                inspiration_selection="none",
                operator_preference=VariationOperator.LOCAL_REFINEMENT,
                instructions="Refine the best candidate aggressively.",
            ),
            SearchStrategy(
                parent_selection="diverse",
                inspiration_selection="diverse",
                operator_preference=VariationOperator.STRUCTURAL_VARIATION,
                instructions="Explore diverse structures.",
            ),
            SearchStrategy(
                parent_selection="ucb",
                inspiration_selection="frontier",
                operator_preference=VariationOperator.FREE_FORM,
                instructions="Balance exploration and exploitation along the frontier.",
            ),
        ]

    async def mutate(
        self,
        parent_strategy: SearchStrategy,
        descriptor: PopulationDescriptor,
        history: list[StrategyRecord],
    ) -> SearchStrategy:
        # Pick a template that differs from the parent to simulate adaptation
        choices = [
            t for t in self._templates if t.parent_selection != parent_strategy.parent_selection
        ]
        if not choices:
            choices = self._templates
        return copy.deepcopy(self.rng.choice(choices))

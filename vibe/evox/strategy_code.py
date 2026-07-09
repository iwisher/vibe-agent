"""Evolvable Python strategy code: generation, execution, and validation."""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field
from typing import Any, Protocol

from vibe.evox.types import Candidate, VariationOperator


class StrategyModule(Protocol):
    """Protocol for a dynamically loaded strategy module."""

    def select_parent(
        self, population: list[Candidate], rng: Any, context: dict[str, Any] | None = None
    ) -> Candidate: ...

    def select_inspiration(
        self, population: list[Candidate], parent: Candidate, rng: Any
    ) -> list[str]: ...

    def select_operator(self, rng: Any) -> VariationOperator: ...


# Default strategy source code used when no code is provided.
DEFAULT_STRATEGY_CODE = '''\
def select_parent(population, rng, context=None):
    """Select a parent uniformly at random."""
    return rng.choice(population)


def select_inspiration(population, parent, rng):
    """No inspiration set by default."""
    return []


def select_operator(rng):
    """Sample a variation operator uniformly at random."""
    import random
    return rng.choice([
        "local_refinement",
        "structural_variation",
        "free_form",
    ])
'''


@dataclass
class EvolvableStrategy:
    """A search strategy represented as executable Python source code.

    The code must define three functions:
        def select_parent(population, rng) -> Candidate
        def select_inspiration(population, parent, rng) -> list[str]
        def select_operator(rng) -> VariationOperator | str

    The functions receive a `random.Random` instance as `rng` and must return
    values consistent with the protocols above.
    """

    code: str = DEFAULT_STRATEGY_CODE
    description: str = "Default random search strategy"
    id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])

    def __post_init__(self):
        self._compiled: Any | None = None
        self._module: Any | None = None

    def compile(self) -> StrategyModule:
        """Compile the strategy code into an executable module-like object.

        Raises:
            SyntaxError: if the code is not valid Python.
            ValueError: if required functions are missing.
        """
        if self._module is not None:
            return self._module

        code = textwrap.dedent(self.code)
        tree = ast.parse(code)
        # Basic safety: reject imports except for random and enum
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in {"random", "math"}:
                        raise ValueError(f"Disallowed import: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                raise ValueError("from ... import is not allowed in strategy code")

        module_globals: dict[str, Any] = {"__builtins__": __builtins__}
        exec(compile(tree, filename="<strategy>", mode="exec"), module_globals)  # noqa: S102

        required = {"select_parent", "select_inspiration", "select_operator"}
        missing = required - set(module_globals.keys())
        if missing:
            raise ValueError(f"Strategy code missing functions: {missing}")

        self._module = _StrategyWrapper(module_globals)
        return self._module

    def to_prompt_text(self) -> str:
        """Serialize strategy for prompts and debugging."""
        return f"Description: {self.description}\n\nCode:\n{self.code}"


def _to_variation_operator(value: Any) -> VariationOperator:
    """Coerce a string or VariationOperator to a VariationOperator."""
    if isinstance(value, VariationOperator):
        return value
    if isinstance(value, str):
        return VariationOperator(value)
    raise ValueError(f"Cannot coerce {value!r} to VariationOperator")


class _StrategyWrapper:
    """Wraps compiled strategy globals into a callable module object."""

    def __init__(self, module_globals: dict[str, Any]):
        self._g = module_globals

    def select_parent(
        self, population: list[Candidate], rng: Any, context: dict[str, Any] | None = None
    ) -> Candidate:
        result = self._g["select_parent"](population, rng, context)
        if not isinstance(result, Candidate):
            raise ValueError("select_parent must return a Candidate")
        return result

    def select_inspiration(
        self, population: list[Candidate], parent: Candidate, rng: Any
    ) -> list[str]:
        result = self._g["select_inspiration"](population, parent, rng)
        if not isinstance(result, list):
            raise ValueError("select_inspiration must return a list of strings")
        return [str(r) for r in result]

    def select_operator(self, rng: Any) -> VariationOperator:
        result = self._g["select_operator"](rng)
        return _to_variation_operator(result)

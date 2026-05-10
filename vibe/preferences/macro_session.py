"""Macro session runner for the preference layer.

A macro is a reusable, multi-step workflow that runs a sequence of queries
against the vibe agent harness.  Steps are Jinja2 templates so that variables
and results from earlier steps can flow into later ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jinja2.sandbox import SandboxedEnvironment


@dataclass
class MacroStep:
    """A single step inside a macro session."""

    name: str
    query: str  # Jinja2 template
    store_result_as: str | None = None
    condition: str | None = None  # Jinja2 expression; skip if false/0/empty/none
    timeout: float = 120.0


@dataclass
class MacroSession:
    """A reusable macro definition."""

    name: str
    description: str = ""
    trigger: str = ""  # e.g. "on_start", "on_command:deploy", or ""
    steps: list[MacroStep] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


class MacroSessionRunner:
    """Loads, persists, and executes macro sessions."""

    MACRO_DIR = Path.home() / ".vibe" / "macros"

    def __init__(self) -> None:
        self._env = SandboxedEnvironment()
        self.MACRO_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def list_macros(self) -> list[str]:
        """Return names of all saved macros."""
        names: list[str] = []
        if not self.MACRO_DIR.exists():
            return names
        for p in self.MACRO_DIR.iterdir():
            if p.suffix in (".yaml", ".yml", ".json"):
                names.append(p.stem)
        return sorted(names)

    def load_macro(self, name: str) -> MacroSession:
        """Load a macro by name.  Raises FileNotFoundError if missing."""
        for ext in (".yaml", ".yml", ".json"):
            path = self.MACRO_DIR / f"{name}{ext}"
            if path.exists():
                raw = path.read_text()
                if ext == ".json":
                    data = json.loads(raw)
                else:
                    data = yaml.safe_load(raw)
                return self._dict_to_macro(data)
        raise FileNotFoundError(f"Macro '{name}' not found in {self.MACRO_DIR}")

    def save_macro(self, macro: MacroSession) -> None:
        """Persist a macro to disk as YAML."""
        path = self.MACRO_DIR / f"{macro.name}.yaml"
        data = self._macro_to_dict(macro)
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(
        self, macro: MacroSession, initial_vars: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute *macro* sequentially, returning the final variable context."""
        context: dict[str, Any] = dict(macro.variables)
        if initial_vars:
            context.update(initial_vars)

        for step in macro.steps:
            # Evaluate condition
            if step.condition is not None:
                condition_result = self._env.from_string(f"{{{{ {step.condition} }}}}").render(
                    context
                )
                if self._is_falsy(condition_result):
                    continue

            # Render query template
            query = self._env.from_string(step.query).render(context)

            # Execute (stubbed — real QueryLoop integration later)
            result = await self._execute_query(query, timeout=step.timeout)

            if step.store_result_as:
                context[step.store_result_as] = result

        return context

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dict_to_macro(self, data: dict[str, Any]) -> MacroSession:
        steps = [
            MacroStep(
                name=s["name"],
                query=s["query"],
                store_result_as=s.get("store_result_as"),
                condition=s.get("condition"),
                timeout=s.get("timeout", 120.0),
            )
            for s in data.get("steps", [])
        ]
        return MacroSession(
            name=data["name"],
            description=data.get("description", ""),
            trigger=data.get("trigger", ""),
            steps=steps,
            variables=data.get("variables", {}),
        )

    def _macro_to_dict(self, macro: MacroSession) -> dict[str, Any]:
        return {
            "name": macro.name,
            "description": macro.description,
            "trigger": macro.trigger,
            "steps": [
                {
                    "name": s.name,
                    "query": s.query,
                    **({"store_result_as": s.store_result_as} if s.store_result_as else {}),
                    **({"condition": s.condition} if s.condition else {}),
                    **({"timeout": s.timeout} if s.timeout != 120.0 else {}),
                }
                for s in macro.steps
            ],
            "variables": macro.variables,
        }

    @staticmethod
    def _is_falsy(value: str) -> bool:
        """Return True if the rendered condition should be treated as false."""
        stripped = value.strip().lower()
        return stripped in ("", "0", "false", "none", "null")

    async def _execute_query(self, query: str, timeout: float = 120.0) -> str:
        """Stub for QueryLoop integration.  Returns the query as the result."""
        # Phase D intentionally leaves this unimplemented; the real hook will
        # inject a QueryLoop instance in a later phase.
        return query

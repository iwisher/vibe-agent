"""Macro session runner — user-defined multi-step workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class MacroStep:
    """A single step in a macro workflow."""

    name: str
    query: str  # Jinja2 template
    store_result_as: str | None = None  # variable name for downstream use
    condition: str | None = None  # Jinja2 condition (skip if false)
    timeout: int = 300


@dataclass
class MacroSession:
    """A user-defined workflow session."""

    name: str
    description: str = ""
    trigger: str | None = None  # "cron: 0 9 * * *" or "manual"
    steps: list[MacroStep] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)


class MacroSessionRunner:
    """Execute macro sessions by converting to DAG and running via QueryLoop."""

    MACRO_DIR = Path.home() / ".vibe" / "macros"

    def __init__(self, query_loop_factory: Any | None = None) -> None:
        self.factory = query_loop_factory

    def list_macros(self) -> list[MacroSession]:
        """List all saved macro sessions."""
        macros = []
        if self.MACRO_DIR.exists():
            for f in self.MACRO_DIR.glob("*.yaml"):
                with open(f) as fh:
                    data = yaml.safe_load(fh)
                    macros.append(self._dict_to_macro(data))
        return macros

    def load_macro(self, name: str) -> MacroSession | None:
        """Load a macro by name."""
        path = self.MACRO_DIR / f"{name}.yaml"
        if not path.exists():
            return None
        with open(path) as f:
            data = yaml.safe_load(f)
        return self._dict_to_macro(data)

    def save_macro(self, macro: MacroSession) -> None:
        """Save a macro to disk."""
        self.MACRO_DIR.mkdir(parents=True, exist_ok=True)
        path = self.MACRO_DIR / f"{macro.name}.yaml"
        with open(path, "w") as f:
            yaml.dump(self._macro_to_dict(macro), f, default_flow_style=False)

    def run(
        self, macro: MacroSession, initial_vars: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a macro session.

        Returns dict of all stored variables.
        SECURITY: Uses SandboxedEnvironment to prevent SSTI/RCE from untrusted inputs.
        """
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment()
        variables = dict(macro.variables)
        variables.update(initial_vars or {})

        results = {}

        for step in macro.steps:
            # Evaluate condition
            if step.condition:
                cond_template = env.from_string(step.condition)
                cond_result = cond_template.render(**variables)
                if cond_result.strip().lower() in ("false", "0", "", "none"):
                    continue

            # Render query with variables
            query_template = env.from_string(step.query)
            query = query_template.render(**variables)

            # Execute via QueryLoop (simplified — real impl would use factory)
            result = self._execute_query(query, step.timeout)

            if step.store_result_as:
                variables[step.store_result_as] = result
                results[step.store_result_as] = result

        return results

    def _execute_query(self, query: str, timeout: int) -> str:
        """Execute a single query. Stub — real impl uses QueryLoop."""
        # Placeholder: real implementation would create a QueryLoop and run
        return f"[Result for: {query[:50]}...]"

    def _dict_to_macro(self, data: dict) -> MacroSession:
        steps = [MacroStep(**s) for s in data.get("steps", [])]
        return MacroSession(
            name=data["name"],
            description=data.get("description", ""),
            trigger=data.get("trigger"),
            steps=steps,
            variables=data.get("variables", {}),
        )

    def _macro_to_dict(self, macro: MacroSession) -> dict:
        return {
            "name": macro.name,
            "description": macro.description,
            "trigger": macro.trigger,
            "steps": [
                {
                    "name": s.name,
                    "query": s.query,
                    "store_result_as": s.store_result_as,
                    "condition": s.condition,
                    "timeout": s.timeout,
                }
                for s in macro.steps
            ],
            "variables": macro.variables,
        }

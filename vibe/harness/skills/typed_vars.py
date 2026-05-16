"""Typed variable substitution with schema validation and defaults for skills.

Replaces string-based {variable} replacement with:
- Type coercion (int, float, bool, str, list, dict)
- Default values for missing variables
- JSON Schema validation for skill inputs
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class VarType(Enum):
    STRING = "string"
    INT = "integer"
    FLOAT = "number"
    BOOL = "boolean"
    LIST = "array"
    DICT = "object"


@dataclass
class SkillVar:
    """Definition of a skill variable with type, default, and validation."""

    name: str
    var_type: VarType = VarType.STRING
    default: Any = None
    required: bool = True
    description: str = ""
    enum: list[Any] | None = None  # Allowed values
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None  # Regex for string validation

    def coerce(self, value: Any) -> Any:
        """Coerce a value to the declared type."""
        if value is None:
            if self.default is not None:
                return self.default
            if self.required:
                raise ValueError(f"Variable '{self.name}' is required but got None")
            return None

        try:
            if self.var_type == VarType.STRING:
                return str(value)
            elif self.var_type == VarType.INT:
                return int(value)
            elif self.var_type == VarType.FLOAT:
                return float(value)
            elif self.var_type == VarType.BOOL:
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes", "on")
                return bool(value)
            elif self.var_type == VarType.LIST:
                if isinstance(value, str):
                    return [v.strip() for v in value.split(",") if v.strip()]
                return list(value)
            elif self.var_type == VarType.DICT:
                if isinstance(value, str):
                    return json.loads(value)
                return dict(value)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            raise ValueError(
                f"Cannot coerce '{self.name}' to {self.var_type.value}: {e}"
            ) from e

        return value

    def validate(self, value: Any) -> list[str]:
        """Validate a coerced value. Returns list of error messages."""
        errors: list[str] = []

        if value is None:
            if self.required and self.default is None:
                errors.append(f"Variable '{self.name}' is required")
            return errors

        if self.enum is not None and value not in self.enum:
            errors.append(
                f"Variable '{self.name}' must be one of {self.enum}, got {value!r}"
            )

        if self.var_type in (VarType.INT, VarType.FLOAT) and isinstance(value, (int, float)):
            if self.min_value is not None and value < self.min_value:
                errors.append(
                    f"Variable '{self.name}' must be >= {self.min_value}, got {value}"
                )
            if self.max_value is not None and value > self.max_value:
                errors.append(
                    f"Variable '{self.name}' must be <= {self.max_value}, got {value}"
                )

        if self.var_type == VarType.STRING and isinstance(value, str) and self.pattern:
            import re

            if not re.match(self.pattern, value):
                errors.append(
                    f"Variable '{self.name}' must match pattern {self.pattern}, got {value!r}"
                )

        return errors


@dataclass
class SkillSchema:
    """Schema defining all variables for a skill."""

    variables: list[SkillVar] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillSchema":
        """Build schema from a JSON Schema-like dict."""
        variables = []
        props = data.get("properties", {})
        required = set(data.get("required", []))

        for name, prop in props.items():
            type_map = {
                "string": VarType.STRING,
                "integer": VarType.INT,
                "number": VarType.FLOAT,
                "boolean": VarType.BOOL,
                "array": VarType.LIST,
                "object": VarType.DICT,
            }
            var = SkillVar(
                name=name,
                var_type=type_map.get(prop.get("type", "string"), VarType.STRING),
                default=prop.get("default"),
                required=name in required,
                description=prop.get("description", ""),
                enum=prop.get("enum"),
                min_value=prop.get("minimum"),
                max_value=prop.get("maximum"),
                pattern=prop.get("pattern"),
            )
            variables.append(var)

        return cls(variables=variables)

    def to_json_schema(self) -> dict[str, Any]:
        """Export to JSON Schema dict."""
        type_map = {
            VarType.STRING: "string",
            VarType.INT: "integer",
            VarType.FLOAT: "number",
            VarType.BOOL: "boolean",
            VarType.LIST: "array",
            VarType.DICT: "object",
        }
        props = {}
        required = []
        for v in self.variables:
            prop: dict[str, Any] = {
                "type": type_map.get(v.var_type, "string"),
                "description": v.description,
            }
            if v.default is not None:
                prop["default"] = v.default
            if v.enum is not None:
                prop["enum"] = v.enum
            if v.min_value is not None:
                prop["minimum"] = v.min_value
            if v.max_value is not None:
                prop["maximum"] = v.max_value
            if v.pattern is not None:
                prop["pattern"] = v.pattern
            props[v.name] = prop
            if v.required:
                required.append(v.name)

        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def apply(self, raw_inputs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Apply schema: coerce types and validate all inputs.

        Returns (coerced_values, error_messages).
        """
        coerced: dict[str, Any] = {}
        errors: list[str] = []

        for var in self.variables:
            raw = raw_inputs.get(var.name, var.default)
            try:
                value = var.coerce(raw)
                coerced[var.name] = value
                var_errors = var.validate(value)
                errors.extend(var_errors)
            except ValueError as e:
                errors.append(str(e))

        # Check for unknown variables
        known = {v.name for v in self.variables}
        for key in raw_inputs:
            if key not in known:
                logger.warning(f"Unknown skill variable: {key}")

        return coerced, errors


class TypedSkillExecutor:
    """Execute skills with typed variable substitution and schema validation."""

    def __init__(self, base_executor: Any | None = None) -> None:
        self.base_executor = base_executor

    def substitute_typed(
        self,
        content: str,
        schema: SkillSchema,
        raw_inputs: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """Substitute variables with type coercion and validation.

        Returns (rendered_content, error_messages).
        """
        coerced, errors = schema.apply(raw_inputs)
        if errors:
            return content, errors

        # Simple {var} replacement with coerced values
        result = content
        for name, value in coerced.items():
            placeholder = f"{{{name}}}"
            if placeholder in result:
                str_value = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
                result = result.replace(placeholder, str_value)

        return result, []

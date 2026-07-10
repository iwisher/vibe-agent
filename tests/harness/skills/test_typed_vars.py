"""Tests for typed skill variable substitution with schema validation."""

import pytest

from vibe.harness.skills.typed_vars import SkillSchema, SkillVar, TypedSkillExecutor, VarType


class TestSkillVar:
    def test_coerce_string(self):
        v = SkillVar("name", VarType.STRING)
        assert v.coerce(42) == "42"

    def test_coerce_int(self):
        v = SkillVar("count", VarType.INT)
        assert v.coerce("42") == 42
        assert v.coerce(3.14) == 3

    def test_coerce_float(self):
        v = SkillVar("rate", VarType.FLOAT)
        assert v.coerce("3.14") == 3.14

    def test_coerce_bool(self):
        v = SkillVar("enabled", VarType.BOOL)
        assert v.coerce("true") is True
        assert v.coerce("false") is False
        assert v.coerce("1") is True
        assert v.coerce(0) is False

    def test_coerce_list(self):
        v = SkillVar("items", VarType.LIST)
        assert v.coerce("a, b, c") == ["a", "b", "c"]
        assert v.coerce([1, 2, 3]) == [1, 2, 3]

    def test_coerce_dict(self):
        v = SkillVar("config", VarType.DICT)
        assert v.coerce('{"key": "value"}') == {"key": "value"}

    def test_default_value(self):
        v = SkillVar("name", VarType.STRING, default="world")
        assert v.coerce(None) == "world"

    def test_required_missing(self):
        v = SkillVar("name", VarType.STRING, required=True)
        with pytest.raises(ValueError, match="required"):
            v.coerce(None)

    def test_validate_enum(self):
        v = SkillVar("mode", VarType.STRING, enum=["fast", "slow"])
        errors = v.validate("fast")
        assert errors == []
        errors = v.validate("medium")
        assert len(errors) == 1

    def test_validate_range(self):
        v = SkillVar("count", VarType.INT, min_value=0, max_value=100)
        assert v.validate(50) == []
        assert len(v.validate(-1)) == 1
        assert len(v.validate(101)) == 1

    def test_validate_pattern(self):
        v = SkillVar("email", VarType.STRING, pattern=r"^[\w.@]+$")
        assert v.validate("test@example.com") == []
        assert len(v.validate("invalid!")) == 1


class TestSkillSchema:
    def test_from_dict(self):
        schema = SkillSchema.from_dict(
            {
                "properties": {
                    "name": {"type": "string", "default": "world"},
                    "count": {"type": "integer", "minimum": 0},
                },
                "required": ["count"],
            }
        )
        assert len(schema.variables) == 2
        assert schema.variables[0].name == "name"
        assert schema.variables[0].default == "world"
        assert schema.variables[1].required is True

    def test_to_json_schema(self):
        schema = SkillSchema(
            variables=[
                SkillVar("name", VarType.STRING, default="world"),
                SkillVar("count", VarType.INT, min_value=0),
            ]
        )
        json_schema = schema.to_json_schema()
        assert json_schema["type"] == "object"
        assert "name" in json_schema["properties"]
        assert json_schema["properties"]["name"]["default"] == "world"

    def test_apply_coerces_and_validates(self):
        schema = SkillSchema(
            variables=[
                SkillVar("name", VarType.STRING, default="world"),
                SkillVar("count", VarType.INT, min_value=0, max_value=10),
            ]
        )
        coerced, errors = schema.apply({"count": "5"})
        assert errors == []
        assert coerced["count"] == 5
        assert coerced["name"] == "world"

    def test_apply_reports_errors(self):
        schema = SkillSchema(
            variables=[
                SkillVar("count", VarType.INT, min_value=0, required=True),
            ]
        )
        coerced, errors = schema.apply({"count": "invalid"})
        assert len(errors) == 1


class TestTypedSkillExecutor:
    def test_substitute_typed(self):
        executor = TypedSkillExecutor()
        schema = SkillSchema(
            variables=[
                SkillVar("name", VarType.STRING, default="world"),
                SkillVar("count", VarType.INT, default=1),
            ]
        )
        result, errors = executor.substitute_typed(
            "Hello {name}, count={count}",
            schema,
            {"name": "Alice", "count": "42"},
        )
        assert errors == []
        assert result == "Hello Alice, count=42"

    def test_substitute_typed_with_validation_error(self):
        executor = TypedSkillExecutor()
        schema = SkillSchema(
            variables=[
                SkillVar("count", VarType.INT, min_value=0),
            ]
        )
        result, errors = executor.substitute_typed(
            "count={count}",
            schema,
            {"count": "-5"},
        )
        assert len(errors) == 1
        assert "count" in errors[0]

    def test_substitute_typed_list_value(self):
        executor = TypedSkillExecutor()
        schema = SkillSchema(
            variables=[
                SkillVar("items", VarType.LIST),
            ]
        )
        result, errors = executor.substitute_typed(
            "items={items}",
            schema,
            {"items": "a, b, c"},
        )
        assert errors == []
        assert '"a"' in result

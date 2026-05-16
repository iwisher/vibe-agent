"""Tests for skill orchestrator — skill composition and sub-agent spawning."""

import pytest

from vibe.harness.skills.orchestrator import (
    OrchestrationResult,
    SkillOrchestrator,
    SubTask,
    TaskStatus,
)


class FakeSkill:
    def __init__(self, name):
        self.name = name


class FakeExecutor:
    """Fake executor that returns skill name + variables."""

    async def execute_async(self, skill, variables):
        return {"skill": skill.name, "vars": variables}

    def execute(self, skill, variables):
        return {"skill": skill.name, "vars": variables}


def fake_executor_factory():
    return FakeExecutor()


class TestSkillOrchestrator:
    @pytest.fixture
    def registry(self):
        return {
            "skill_a": FakeSkill("skill_a"),
            "skill_b": FakeSkill("skill_b"),
            "skill_c": FakeSkill("skill_c"),
        }

    @pytest.fixture
    def orchestrator(self, registry):
        return SkillOrchestrator(registry, fake_executor_factory)

    @pytest.mark.asyncio
    async def test_run_single_skill(self, orchestrator):
        result = await orchestrator.run_skill("skill_a", {"x": 1})
        assert result["skill"] == "skill_a"
        assert result["vars"]["x"] == 1

    @pytest.mark.asyncio
    async def test_run_skill_not_found(self, orchestrator):
        with pytest.raises(ValueError, match="not found"):
            await orchestrator.run_skill("nonexistent")

    @pytest.mark.asyncio
    async def test_run_parallel(self, orchestrator):
        results = await orchestrator.run_parallel([
            ("skill_a", {"x": 1}),
            ("skill_b", {"y": 2}),
            ("skill_c", {"z": 3}),
        ])
        assert len(results) == 3
        assert results[0]["skill"] == "skill_a"
        assert results[1]["skill"] == "skill_b"
        assert results[2]["skill"] == "skill_c"

    def test_resolve_dag_linear(self, orchestrator):
        tasks = [
            SubTask("t1", "skill_a"),
            SubTask("t2", "skill_b", dependencies=["t1"]),
            SubTask("t3", "skill_c", dependencies=["t2"]),
        ]
        waves = orchestrator._resolve_dag(tasks)
        assert len(waves) == 3
        assert [t.task_id for t in waves[0]] == ["t1"]
        assert [t.task_id for t in waves[1]] == ["t2"]
        assert [t.task_id for t in waves[2]] == ["t3"]

    def test_resolve_dag_parallel(self, orchestrator):
        tasks = [
            SubTask("t1", "skill_a"),
            SubTask("t2", "skill_b"),
            SubTask("t3", "skill_c", dependencies=["t1", "t2"]),
        ]
        waves = orchestrator._resolve_dag(tasks)
        assert len(waves) == 2
        assert len(waves[0]) == 2  # t1, t2 parallel
        assert len(waves[1]) == 1  # t3 after both

    def test_resolve_dag_circular(self, orchestrator):
        tasks = [
            SubTask("t1", "skill_a", dependencies=["t2"]),
            SubTask("t2", "skill_b", dependencies=["t1"]),
        ]
        with pytest.raises(ValueError, match="Cannot resolve"):
            orchestrator._resolve_dag(tasks)

    @pytest.mark.asyncio
    async def test_run_dag(self, orchestrator):
        tasks = [
            SubTask("t1", "skill_a", variables={"x": 1}),
            SubTask("t2", "skill_b", variables={"y": 2}),
            SubTask("t3", "skill_c", dependencies=["t1", "t2"]),
        ]
        result = await orchestrator.run_dag(tasks)
        assert result.success is True
        assert "t1" in result.outputs
        assert "t2" in result.outputs
        assert "t3" in result.outputs
        assert result.execution_order == ["t1", "t2", "t3"]

    @pytest.mark.asyncio
    async def test_run_dag_with_failure(self, orchestrator):
        # Add a skill that doesn't exist to cause failure
        tasks = [
            SubTask("t1", "skill_a"),
            SubTask("t2", "nonexistent"),
        ]
        result = await orchestrator.run_dag(tasks)
        assert result.success is False
        assert "t2" in result.errors

    @pytest.mark.asyncio
    async def test_call_skill_from_skill(self, orchestrator):
        result = await orchestrator.call_skill_from_skill(
            "skill_a", "skill_b", {"foo": "bar"}
        )
        assert result["skill"] == "skill_b"
        assert result["vars"]["foo"] == "bar"

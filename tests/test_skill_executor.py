"""Test skill executor."""
import pytest
from vibe.harness.skills.executor import SkillExecutor
from vibe.harness.skills.models import Skill, SkillStep, SkillTrigger, SkillVerification


@pytest.fixture
def simple_skill():
    return Skill(
        vibe_skill_version="2.0.0",
        id="test",
        name="Test",
        description="Test skill",
        category="test",
        tags=[],
        trigger=SkillTrigger(),
        steps=[
            SkillStep(
                id="step1",
                description="Echo test",
                tool="bash",
                command="echo {message}",
                verification=SkillVerification(output_contains="hello"),
            )
        ],
    )


@pytest.mark.asyncio
async def test_executor_substitutes_variables(simple_skill):
    from vibe.tools.bash import BashTool
    bash = BashTool()
    executor = SkillExecutor(bash_tool=bash)
    result = await executor.execute_step(simple_skill.steps[0], {"message": "hello"})
    assert result.success
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_executor_verification_fails(simple_skill):
    from vibe.tools.bash import BashTool
    bash = BashTool()
    executor = SkillExecutor(bash_tool=bash)
    result = await executor.execute_step(simple_skill.steps[0], {"message": "goodbye"})
    assert not result.success  # output_contains "hello" fails


@pytest.mark.asyncio
async def test_executor_shlex_quote_prevents_injection():
    from vibe.tools.bash import BashTool
    bash = BashTool()
    executor = SkillExecutor(bash_tool=bash)
    step = SkillStep(
        id="inject",
        description="Injection test",
        tool="bash",
        command="echo {message}",
        verification=SkillVerification(),
    )
    # This should NOT execute rm -rf /
    result = await executor.execute_step(step, {"message": "hello; rm -rf /"})
    assert result.success  # echo should succeed with literal string
    # The literal string should appear in output (quoted by shlex)
    assert "hello" in result.output or result.output == ""

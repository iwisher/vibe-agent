"""Test skill pydantic models."""
import pytest
from vibe.harness.skills.models import Skill, SkillStep, SkillTrigger, SkillVerification


def test_skill_model_creation():
    skill = Skill(
        vibe_skill_version="2.0.0",
        id="test-skill",
        name="Test Skill",
        description="A test skill",
        category="test",
        tags=["test", "demo"],
        trigger=SkillTrigger(patterns=["test"], required_tools=["bash"]),
        steps=[
            SkillStep(
                id="step1",
                description="Hello",
                tool="bash",
                command="echo hello",
                verification=SkillVerification(exit_code=0),
            )
        ],
    )
    assert skill.id == "test-skill"
    assert len(skill.steps) == 1


def test_skill_validation_missing_required():
    with pytest.raises(ValueError):
        Skill(
            vibe_skill_version="2.0.0",
            id="",
            name="",
            description="",
            trigger=SkillTrigger(),
            steps=[],
        )


def test_step_id_uniqueness():
    with pytest.raises(ValueError):
        Skill(
            vibe_skill_version="2.0.0",
            id="test",
            name="Test",
            description="Test",
            trigger=SkillTrigger(),
            steps=[
                SkillStep(id="dup", description="A", tool="bash", command="echo a"),
                SkillStep(id="dup", description="B", tool="bash", command="echo b"),
            ],
        )


def test_skill_id_format_validation():
    with pytest.raises(ValueError, match="alphanumeric"):
        Skill(
            vibe_skill_version="2.0.0",
            id="../../etc",
            name="Evil",
            description="Evil",
            trigger=SkillTrigger(),
            steps=[SkillStep(id="s1", description="A", tool="bash", command="echo a")],
        )


def test_skill_id_with_hyphens_allowed():
    skill = Skill(
        vibe_skill_version="2.0.0",
        id="my-skill-123",
        name="My Skill",
        description="Test",
        trigger=SkillTrigger(),
        steps=[SkillStep(id="s1", description="A", tool="bash", command="echo a")],
    )
    assert skill.id == "my-skill-123"

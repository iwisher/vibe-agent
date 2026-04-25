"""Skill pydantic models."""
import re

from pydantic import BaseModel, Field, model_validator


class SkillVerification(BaseModel):
    exit_code: int | None = None
    output_contains: str | None = None
    file_exists: str | None = None
    json_has_keys: list[str] = Field(default_factory=list)


class SkillStep(BaseModel):
    id: str
    description: str
    script: str | None = None
    tool: str
    command: str
    condition: str | None = None
    inputs: list[dict] = Field(default_factory=list)
    outputs: list[dict] = Field(default_factory=list)
    verification: SkillVerification = Field(default_factory=SkillVerification)


class SkillTrigger(BaseModel):
    patterns: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)


class Skill(BaseModel):
    vibe_skill_version: str
    id: str
    name: str
    description: str
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    trigger: SkillTrigger = Field(default_factory=SkillTrigger)
    steps: list[SkillStep]
    pitfalls: list[str] = Field(default_factory=list)
    examples: list[dict] = Field(default_factory=list)
    variables: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_unique_step_ids(self):
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Step IDs must be unique")
        return self

    @model_validator(mode="after")
    def check_id_and_name(self):
        if not re.match(r'^[a-zA-Z0-9_-]+$', self.id):
            raise ValueError("Skill id must contain only alphanumeric characters, hyphens, and underscores")
        if not self.id.strip():
            raise ValueError("Skill id is required")
        if not self.name.strip():
            raise ValueError("Skill name is required")
        return self

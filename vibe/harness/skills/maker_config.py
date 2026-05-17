"""Configuration for the SkillMakerPipeline."""

from pydantic import BaseModel, Field


class SkillMakerConfig(BaseModel):
    """Configuration for autonomous skill generation."""

    enabled: bool = False
    min_pattern_frequency: int = Field(
        default=3,
        ge=2,
        description="Minimum wiki pages with same tag to trigger pattern detection",
    )
    max_skills_per_session: int = Field(
        default=1,
        ge=0,
        description="Max skill proposals per session",
    )
    sandbox_timeout_seconds: int = Field(
        default=30,
        ge=5,
        description="Timeout for sandbox validation",
    )
    auto_install_approved: bool = Field(
        default=False,
        description="Auto-install skills that pass validation with no risks",
    )
    llm_model: str = Field(
        default="",
        description="Model to use for skill generation (empty = use loop's model)",
    )
    excluded_tags: list[str] = Field(
        default_factory=lambda: ["session", "telemetry", "system"],
        description="Tags to exclude from pattern detection",
    )

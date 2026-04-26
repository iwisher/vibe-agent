"""Pydantic-based configuration schema for vibe-agent.

Replaces dict-based config with type-safe Pydantic models.
Supports validation, defaults, env var overrides, and .env file loading.

Backward compatibility: Maintains old config interface (llm.default_model, llm.base_url, logging, etc.)
"""

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogConfig(BaseModel):
    """Logging configuration."""
    enabled: bool = True
    log_dir: str = "./logs"
    max_file_size_mb: float = Field(default=10.0, ge=0.1)
    retention_days: int = Field(default=7, ge=1)
    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


class LLMConfig(BaseModel):
    """LLM configuration (backward compatible with old config)."""
    default_model: str = "default"
    base_url: str = "http://localhost:11434/v1"
    api_key: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    timeout: float = Field(default=60.0, ge=1.0)
    fallback_chain: list[str] = Field(default_factory=list)

    @field_validator("temperature")
    @classmethod
    def validate_temp(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v


class ModelConfig(BaseModel):
    """Model-specific configuration."""
    provider: str = "openai"
    model: str = "gpt-4o"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
    timeout: float = Field(default=60.0, ge=1.0)

    @field_validator("temperature")
    @classmethod
    def validate_temp(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v


class PlannerConfig(BaseModel):
    """Planner configuration."""
    enabled: bool = True
    use_embeddings: bool = False
    embedding_model_path: Optional[str] = None
    llm_routing: bool = False
    cache_ttl: int = Field(default=3600, ge=60)
    max_llm_tools: int = Field(default=10, ge=1, le=50)


class TraceStoreConfig(BaseModel):
    """Trace store configuration."""
    enabled: bool = True
    storage_type: str = Field(default="sqlite", pattern=r"^(sqlite|json|memory)$")
    db_path: Optional[str] = None
    max_entries: int = Field(default=10000, ge=100)
    retention_days: int = Field(default=30, ge=1)


class EvalConfig(BaseModel):
    """Evaluation configuration."""
    enabled: bool = True
    parallel: bool = True
    max_workers: int = Field(default=4, ge=1, le=16)
    timeout: float = Field(default=300.0, ge=10.0)
    output_dir: str = "./eval_results"


class SecurityConfig(BaseModel):
    """Security configuration."""
    enable_constraints: bool = True
    max_file_size_mb: float = Field(default=10.0, ge=0.1)
    allowed_paths: list[str] = Field(default_factory=lambda: ["./"])
    blocked_commands: list[str] = Field(default_factory=list)
    require_approval: bool = True


class VibeConfig(BaseSettings):
    """Root configuration for vibe-agent.

    Loads from:
    1. Environment variables (VIBE_* prefix)
    2. .env file
    3. Default values

    Backward compatibility: Maintains old interface with llm, logging, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="VIBE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core settings
    debug: bool = False
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    workspace_dir: str = "./workspace"

    # Backward compatible sub-configs
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LogConfig = Field(default_factory=LogConfig)

    # New sub-configs
    model: ModelConfig = Field(default_factory=ModelConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    trace_store: TraceStoreConfig = Field(default_factory=TraceStoreConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    # Legacy compatibility
    api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    base_url: Optional[str] = None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v_upper

    def get_fallback_chain(self) -> list[str]:
        """Get fallback chain from LLM config."""
        return self.llm.fallback_chain or []

    def resolve_api_key(self) -> Optional[str]:
        """Resolve API key from various sources."""
        return self.llm.api_key or self.api_key or os.environ.get("OPENAI_API_KEY")

    @classmethod
    def load(cls) -> "VibeConfig":
        """Load configuration from default sources."""
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VibeConfig":
        """Load from a dictionary (legacy compatibility)."""
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "VibeConfig":
        """Load from a YAML/JSON file."""
        path = Path(path)
        if not path.exists():
            return cls()

        import json
        if path.suffix == ".json":
            with open(path) as f:
                data = json.load(f)
        else:
            try:
                import yaml
                with open(path) as f:
                    data = yaml.safe_load(f)
            except ImportError:
                raise ImportError("PyYAML required for YAML config files. Install with: pip install pyyaml")

        return cls.model_validate(data)

    def to_dict(self) -> dict[str, Any]:
        """Export to dictionary."""
        return self.model_dump()

    def merge_env_overrides(self) -> "VibeConfig":
        """Re-load with environment variable overrides."""
        return self.model_copy()

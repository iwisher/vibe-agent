"""Configuration and feature flags for ClaudeWorker.

All new features are behind feature flags for safe incremental rollout.
Each flag includes fallback behavior and rollback triggers.
"""

import os
from dataclasses import dataclass
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


def _env_to_bool(value: Optional[str]) -> bool:
    """Convert environment variable to boolean."""
    if not value:
        return False
    return value.lower() in ("true", "1", "yes", "on", "enabled")


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean from environment variable."""
    value = os.getenv(name, str(default).lower())
    return value.lower() in ("true", "1", "yes", "on", "enabled")


def _env_int(name: str, default: int) -> int:
    """Parse integer from environment variable."""
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(name: str, default: float) -> float:
    """Parse float from environment variable."""
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_str(name: str, default: str) -> str:
    """Parse string from environment variable."""
    return os.getenv(name, default)


class FeatureFlags(BaseSettings):
    """
    Feature flags for safe incremental rollout.
    Each flag includes fallback behavior and rollback triggers.
    """
    
    # INFRASTRUCTURE FLAGS
    use_single_writer: bool = Field(default=False)
    """
    WHEN FALSE: Uses synchronous sqlite3.connect() per operation (current behavior)
    WHEN TRUE: Uses async single-writer queue with run_in_executor()
    ROLLBACK TRIGGER: DB write latency >100ms or queue backlog >100 ops
    """
    
    use_memory_cache: bool = Field(default=False)
    """
    WHEN FALSE: Queries SQLite directly with full table scans
    WHEN TRUE: Uses in-memory LRU cache for task queries
    ROLLBACK TRIGGER: Memory usage >500MB or cache miss rate >80%
    """
    
    # UX FLAGS
    enable_task_edit: bool = Field(default=False)
    """
    WHEN FALSE: Task updates rejected with 405 Method Not Allowed
    WHEN TRUE: PATCH /tasks/{id} endpoint enabled
    ROLLBACK TRIGGER: Edit corruption or data loss
    """
    
    enable_batch_ops: bool = Field(default=False)
    """
    WHEN FALSE: Batch endpoints return 404
    WHEN TRUE: POST /tasks/batch/{cancel|delete|retry} enabled
    ROLLBACK TRIGGER: Accidental mass operation
    """
    
    enable_templates: bool = Field(default=False)
    """
    WHEN FALSE: Template endpoints return 404
    WHEN TRUE: Template CRUD endpoints enabled
    ROLLBACK TRIGGER: Template corruption
    """
    
    enable_export: bool = Field(default=False)
    """
    WHEN FALSE: Export endpoints return 404
    WHEN TRUE: GET /tasks/export/{format} enabled
    ROLLBACK TRIGGER: Export data leakage
    """
    
    @classmethod
    def from_env(cls) -> "FeatureFlags":
        """Create FeatureFlags from environment variables."""
        return cls(
            use_single_writer=_env_to_bool(os.getenv("CW_USE_SINGLE_WRITER")),
            use_memory_cache=_env_to_bool(os.getenv("CW_USE_MEMORY_CACHE")),
            enable_task_edit=_env_to_bool(os.getenv("CW_ENABLE_TASK_EDIT")),
            enable_batch_ops=_env_to_bool(os.getenv("CW_ENABLE_BATCH_OPS")),
            enable_templates=_env_to_bool(os.getenv("CW_ENABLE_TEMPLATES")),
            enable_export=_env_to_bool(os.getenv("CW_ENABLE_EXPORT")),
        )
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "use_memory_cache": self.use_memory_cache,
            "enable_task_edit": self.enable_task_edit,
            "enable_batch_ops": self.enable_batch_ops,
            "enable_templates": self.enable_templates,
            "enable_export": self.enable_export,
        }


@dataclass
class CacheConfig:
    """Configuration for memory cache."""
    max_entries: int = 1000
    max_memory_mb: float = 100.0
    ttl_seconds: int = 3600  # 1 hour
    
    @classmethod
    def from_env(cls) -> "CacheConfig":
        return cls(
            max_entries=_env_int("CW_CACHE_MAX_ENTRIES", 1000),
            max_memory_mb=_env_float("CW_CACHE_MAX_MEMORY_MB", 100.0),
            ttl_seconds=_env_int("CW_CACHE_TTL_SECONDS", 3600),
        )


@dataclass
class TemplateConfig:
    """Configuration for task templates."""
    storage_path: str = ".claudeworker/templates"
    max_templates: int = 100
    max_template_size_kb: int = 100  # Max size for a single template
    
    @classmethod
    def from_env(cls) -> "TemplateConfig":
        return cls(
            storage_path=_env_str("CW_TEMPLATES_PATH", ".claudeworker/templates"),
            max_templates=_env_int("CW_MAX_TEMPLATES", 100),
            max_template_size_kb=_env_int("CW_MAX_TEMPLATE_SIZE_KB", 100),
        )


@dataclass
class ExportConfig:
    """Configuration for task export."""
    max_export_size: int = 10000  # Max tasks to export at once
    export_timeout_seconds: int = 30
    allowed_formats: tuple = ("md", "json", "csv")
    
    @classmethod
    def from_env(cls) -> "ExportConfig":
        formats_str = _env_str("CW_EXPORT_FORMATS", "md,json,csv")
        return cls(
            max_export_size=_env_int("CW_MAX_EXPORT_SIZE", 10000),
            export_timeout_seconds=_env_int("CW_EXPORT_TIMEOUT", 30),
            allowed_formats=tuple(formats_str.split(",")),
        )


class Settings(BaseSettings):
    """Application settings."""
    
    # Database
    database_path: str = Field(default=".claudeworker/tasks.db")
    
    # API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_secret: Optional[str] = Field(default=None)
    
    # Celery
    celery_broker_url: str = Field(default="redis://localhost:6379/0")
    celery_result_backend: str = Field(default="redis://localhost:6379/0")
    
    # Memory
    memory_max_entries: int = Field(default=1000)
    memory_max_memory_mb: float = Field(default=100.0)
    memory_ttl_seconds: int = Field(default=3600)
    
    # LLM
    openai_api_key: Optional[str] = Field(default=None)
    default_model: str = Field(default="claude-3-sonnet-20240229")
    
    # Logging
    log_level: str = Field(default="INFO")
    
    class Config:
        env_prefix = "CW_"
        case_sensitive = False


@dataclass
class ClaudeWorkerConfig:
    """Main configuration class."""
    feature_flags: FeatureFlags
    cache_config: CacheConfig
    template_config: TemplateConfig
    export_config: ExportConfig
    
    # Global config instance
    _instance: Optional["ClaudeWorkerConfig"] = None
    
    @classmethod
    def get(cls) -> "ClaudeWorkerConfig":
        """Get or create global config instance."""
        if cls._instance is None:
            cls._instance = cls(
                feature_flags=FeatureFlags.from_env(),
                cache_config=CacheConfig.from_env(),
                template_config=TemplateConfig.from_env(),
                export_config=ExportConfig.from_env(),
            )
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """Reset global config instance (for testing)."""
        cls._instance = None


# Global settings instance
_settings: Optional[Settings] = None
_feature_flags: Optional[FeatureFlags] = None


def get_settings() -> Settings:
    """Get global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_feature_flags() -> FeatureFlags:
    """Get global feature flags instance."""
    global _feature_flags
    if _feature_flags is None:
        _feature_flags = FeatureFlags.from_env()
    return _feature_flags


def reload_feature_flags() -> FeatureFlags:
    """Reload feature flags from environment."""
    global _feature_flags
    _feature_flags = FeatureFlags.from_env()
    return _feature_flags


# Convenience functions for checking feature flags

def is_memory_cache_enabled() -> bool:
    """Check if memory cache is enabled."""
    return ClaudeWorkerConfig.get().feature_flags.use_memory_cache


def is_task_edit_enabled() -> bool:
    """Check if task editing is enabled."""
    return ClaudeWorkerConfig.get().feature_flags.enable_task_edit


def is_batch_ops_enabled() -> bool:
    """Check if batch operations are enabled."""
    return ClaudeWorkerConfig.get().feature_flags.enable_batch_ops


def is_templates_enabled() -> bool:
    """Check if templates are enabled."""
    return ClaudeWorkerConfig.get().feature_flags.enable_templates


def is_export_enabled() -> bool:
    """Check if export is enabled."""
    return ClaudeWorkerConfig.get().feature_flags.enable_export

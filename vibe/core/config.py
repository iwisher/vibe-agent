"""Configuration schema for vibe-agent.

Provides two compatible configuration APIs:

1. Legacy class-based API (file-based YAML, used by older tests):
   VibeConfig.load(path=..., auto_create=True)
   Exposes: FallbackConfig, FileSafetyConfig, EnvSanitizationConfig,
            SandboxConfig, AuditConfig, SecurityConfig (full), ProviderRegistry

2. New pydantic-settings API (env-var-driven, used by new code):
   VibeConfig() — reads from VIBE_* env vars + .env file

Both APIs live on VibeConfig; the file-based load() method populates all fields
from YAML then applies env overrides on top.

Helper functions:
  _parse_bool, _parse_float, _parse_int, _parse_list — low-level env parsers
  _parse_providers — build ProviderRegistry from raw YAML dict
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Re-exported from provider_registry for convenience
# ---------------------------------------------------------------------------

from vibe.core.provider_registry import ProviderProfile, ProviderRegistry  # noqa: F401


# ---------------------------------------------------------------------------
# Parse helpers (legacy env-var API)
# ---------------------------------------------------------------------------


def _parse_bool(val: str) -> bool:
    """Parse a string into a bool. Raises ValueError for invalid values."""
    if not val:
        return False
    lower = val.lower()
    if lower in ("true", "1", "yes", "on"):
        return True
    if lower in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"Cannot parse bool from {val!r}; use true/false/1/0/yes/no/on/off")


def _parse_float(env_var: str, default: float) -> float:
    """Read an env var as a float, returning default if missing."""
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Cannot parse float from {env_var}={raw!r}")


def _parse_int(env_var: str, default: int) -> int:
    """Read an env var as an int, returning default if missing."""
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Cannot parse int from {env_var}={raw!r}")


def _parse_list(raw: Optional[str], default: Any) -> list:
    """Parse a comma-separated string into a list.

    Args:
        raw: Raw string value (comma-separated), or None.
        default: Default value if raw is None. Must be a list.

    Raises:
        ValueError: If default is not a list or None.
    """
    if raw is None:
        if default is None:
            return []
        if not isinstance(default, list):
            raise ValueError(f"Default for _parse_list must be a list, got {type(default)}")
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_providers(raw: dict[str, Any], llm_config: "LLMConfig") -> ProviderRegistry:
    """Build a ProviderRegistry from a raw YAML 'providers' dict.

    Backward compat: if raw is empty, synthesises a 'default' provider from llm_config.

    Args:
        raw: Dict of provider_name -> dict with keys: base_url, adapter, api_key_env_var, timeout.
        llm_config: LLM config for backward-compat fallback.

    Returns:
        Populated ProviderRegistry.

    Raises:
        ValueError: If a provider entry is malformed.
    """
    if not raw:
        # Backward compat: synthesise 'default' provider from LLMConfig
        default_profile = ProviderProfile(
            name="default",
            base_url=llm_config.base_url,
            adapter_type="openai",
            api_key=llm_config.api_key,
            api_key_env_var="LLM_API_KEY",
            timeout=llm_config.timeout,
        )
        return ProviderRegistry({"default": default_profile})

    providers: dict[str, ProviderProfile] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(
                f"Provider '{name}' config must be a mapping, got {type(cfg).__name__}"
            )
        base_url = cfg.get("base_url")
        if not base_url:
            raise ValueError(f"Provider '{name}' is missing required 'base_url'")
        providers[name] = ProviderProfile(
            name=name,
            base_url=base_url,
            adapter_type=cfg.get("adapter", "openai"),
            api_key=cfg.get("api_key"),
            api_key_env_var=cfg.get("api_key_env_var", "LLM_API_KEY"),
            timeout=float(cfg.get("timeout", 120.0)),
            default_model=cfg.get("default_model"),
            extra_headers=cfg.get("extra_headers", {}),
        )
    return ProviderRegistry(providers)


# ---------------------------------------------------------------------------
# Pydantic sub-configs
# ---------------------------------------------------------------------------


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
    base_url: str = "http://localhost:11434"
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
    scorecard_dir: str = Field(
        default_factory=lambda: os.path.expanduser("~/.vibe/scorecards")
    )


# ---------------------------------------------------------------------------
# Security sub-configs (full expanded for test compatibility)
# ---------------------------------------------------------------------------


class FileSafetyConfig(BaseModel):
    """File system safety rules."""

    write_denylist_enabled: bool = True
    read_blocklist_enabled: bool = True
    safe_root: Optional[str] = None  # Restrict writes to this directory

    @field_validator("safe_root")
    @classmethod
    def validate_safe_root(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        resolved = Path(v).resolve()
        if not resolved.exists():
            raise ValueError(f"safe_root does not exist: {v!r}")
        return str(resolved)


class EnvSanitizationConfig(BaseModel):
    """Environment variable sanitization."""

    enabled: bool = True
    block_path_overrides: bool = True
    strip_shell_env: bool = True
    secret_prefixes: list[str] = Field(
        default_factory=lambda: ["*_API_KEY", "*_TOKEN", "*_SECRET", "AWS_*", "GITHUB_*"]
    )


class SandboxConfig(BaseModel):
    """Sandbox execution configuration."""

    backend: str = Field(default="local")
    auto_approve_in_sandbox: bool = False

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, v: str) -> str:
        valid = {"local", "docker", "vm", "sandbox-exec"}
        if v not in valid:
            raise ValueError(f"sandbox.backend must be one of {sorted(valid)}, got {v!r}")
        return v


class AuditConfig(BaseModel):
    """Security audit log configuration."""

    log_path: str = Field(
        default_factory=lambda: os.path.expanduser("~/.vibe/logs/security.log")
    )
    max_events: int = Field(default=10000)
    redact_in_logs: bool = True

    @field_validator("max_events")
    @classmethod
    def validate_max_events(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_events must be >= 1")
        return v



class SecurityConfig(BaseModel):
    """Full security configuration for vibe-agent tools.

    Controls approval mode, file safety, env sanitization, sandbox, and audit.
    """

    approval_mode: str = Field(default="smart")
    dangerous_patterns_enabled: bool = True
    secret_redaction: bool = True
    audit_logging: bool = True
    fail_closed: bool = True

    file_safety: FileSafetyConfig = Field(default_factory=FileSafetyConfig)
    env_sanitization: EnvSanitizationConfig = Field(default_factory=EnvSanitizationConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    # Legacy flat fields (kept for backward compat with old SecurityConfig users)
    enable_constraints: bool = True
    max_file_size_mb: float = Field(default=10.0, ge=0.1)
    allowed_paths: list[str] = Field(default_factory=lambda: ["./"])
    blocked_commands: list[str] = Field(default_factory=list)
    require_approval: bool = True

    @field_validator("approval_mode")
    @classmethod
    def validate_approval_mode(cls, v: str) -> str:
        valid = {"manual", "smart", "auto"}
        if v not in valid:
            raise ValueError(f"approval_mode must be one of {sorted(valid)}, got {v!r}")
        return v

    def is_approval_required(self) -> bool:
        """Return True if human approval is required before tool execution."""
        return self.approval_mode in ("manual", "smart")

    def is_auto_approve(self) -> bool:
        """Return True if tools run without approval."""
        return self.approval_mode == "auto"


# ---------------------------------------------------------------------------
# Legacy FallbackConfig (circuit-breaker + model chain)
# ---------------------------------------------------------------------------


class FallbackConfig(BaseModel):
    """Circuit-breaker and fallback chain configuration.

    Exposes legacy env vars:
      VIBE_CB_THRESHOLD  → circuit_breaker_threshold
      VIBE_CB_COOLDOWN   → circuit_breaker_cooldown
      VIBE_FALLBACK_CHAIN → chain
    """

    enabled: bool = True
    chain: list[str] = Field(default_factory=lambda: ["default"])
    circuit_breaker_threshold: int = Field(
        default_factory=lambda: _parse_int("VIBE_CB_THRESHOLD", 5)
    )
    circuit_breaker_cooldown: float = Field(
        default_factory=lambda: _parse_float("VIBE_CB_COOLDOWN", 60.0)
    )
    # Fields used by health_check.py
    health_check_timeout: float = 5.0
    max_retries: int = 3

    @field_validator("chain", mode="before")
    @classmethod
    def parse_chain_from_env(cls, v: Any) -> Any:
        env_chain = os.environ.get("VIBE_FALLBACK_CHAIN")
        if env_chain is not None:
            return _parse_list(env_chain, None)
        return v



# ---------------------------------------------------------------------------
# Tripartite memory (Phase 1a)
# ---------------------------------------------------------------------------


class FlashModelConfig(BaseModel):
    """Configuration for the lightweight Flash LLM used for quality gates."""

    base_url: str = "http://localhost:11434/v1"
    model: str = "qwen3:1.7b"
    api_key: Optional[str] = None
    timeout: float = Field(default=15.0, ge=1.0, le=120.0)


class WikiConfig(BaseModel):
    """LLM Wiki storage configuration."""

    auto_extract: bool = False
    base_path: str = "~/.vibe/wiki"
    extraction_prompt: Optional[str] = None
    novelty_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    default_ttl_days: int = Field(default=30, ge=1)
    extraction_batch_size: int = Field(default=5, ge=1, le=50)
    extraction_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    # Optional lightweight model for contradiction detection / confidence scoring
    flash_model: Optional[FlashModelConfig] = None


class PageIndexConfig(BaseModel):
    """PageIndex routing configuration."""

    index_path: str = "~/.vibe/memory/index.json"
    rebuild_on_change: bool = True
    max_nodes_per_index: int = Field(default=100, ge=10)
    token_threshold: int = Field(default=4000, ge=100)
    routing_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)


class RLMConfig(BaseModel):
    """Recursive Language Model config — Phase 2 telemetry-triggered activation."""

    enabled: bool = False
    trigger_threshold_chars: int = Field(default=100_000, ge=1_000)
    trigger_threshold_compaction_pct: float = Field(default=0.3, ge=0.0, le=1.0)
    trigger_window_sessions: int = Field(default=50, ge=5, le=500)
    min_sessions_before_trigger: int = Field(default=10, ge=1, le=100)
    rlm_model_path: Optional[str] = None


class TripartiteMemoryConfig(BaseModel):
    """Tripartite Memory System configuration."""

    enabled: bool = False
    wiki: WikiConfig = Field(default_factory=WikiConfig)
    pageindex: PageIndexConfig = Field(default_factory=PageIndexConfig)
    rlm: RLMConfig = Field(default_factory=RLMConfig)


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------


class VibeConfig(BaseSettings):
    """Root configuration for vibe-agent.

    Supports two loading strategies:
    1. ``VibeConfig()`` — pydantic-settings, reads VIBE_* env vars + .env
    2. ``VibeConfig.load(path=..., auto_create=True)`` — YAML file + env overrides
       (legacy API; maintains full backward compatibility)
    """

    model_config = SettingsConfigDict(
        env_prefix="VIBE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    debug: bool = False
    log_level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    workspace_dir: str = "./workspace"

    # Sub-configs
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LogConfig = Field(default_factory=LogConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    trace_store: TraceStoreConfig = Field(default_factory=TraceStoreConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    memory: TripartiteMemoryConfig = Field(default_factory=TripartiteMemoryConfig)

    # Legacy flat compat
    api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    base_url: Optional[str] = None

    # These fields are populated by load() — not pydantic-settings driven
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    providers: Optional[Any] = Field(default=None)  # ProviderRegistry (set in load())
    models: dict[str, Any] = Field(default_factory=dict)
    resolved_model: Optional[str] = None

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v_upper

    # ------------------------------------------------------------------
    # Legacy file-based load() API
    # ------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        path: Optional[Path | str] = None,
        auto_create: bool = True,
    ) -> "VibeConfig":
        """Load config from a YAML file with env var overrides.

        Args:
            path: Path to config YAML file.  If None, uses ~/.vibe/config.yaml.
            auto_create: If True, creates the file with defaults when missing.

        Returns:
            Fully populated VibeConfig.

        Raises:
            ValueError: If the file contains invalid YAML or a non-dict top-level.
        """
        import yaml

        if path is None:
            path = Path.home() / ".vibe" / "config.yaml"
        path = Path(path)

        # ---- Read or create file ----
        raw: dict[str, Any] = {}
        if path.exists():
            text = path.read_text(encoding="utf-8")
            try:
                raw = yaml.safe_load(text) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"Invalid YAML in {path}: {e}") from e
            if not isinstance(raw, dict):
                raise ValueError(
                    f"Config file {path} must contain a top-level mapping, got {type(raw).__name__}"
                )
        elif auto_create:
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = _default_yaml_dict()
            path.write_text(yaml.dump(raw, default_flow_style=False), encoding="utf-8")
        # else: raw stays empty → all defaults

        # ---- Build sub-configs from file ----
        llm_raw = raw.get("llm", {})
        llm_cfg = LLMConfig(
            default_model=os.environ.get("VIBE_MODEL", llm_raw.get("default_model", "default")),
            base_url=os.environ.get("VIBE_BASE_URL", llm_raw.get("base_url", "http://localhost:11434")),
            api_key=llm_raw.get("api_key"),
            timeout=_parse_float("VIBE_TIMEOUT", float(llm_raw.get("timeout", 60.0))),
            fallback_chain=_parse_list(
                os.environ.get("VIBE_FALLBACK_CHAIN"),
                llm_raw.get("fallback_chain", []),
            ),
        )

        # ---- FallbackConfig ----
        fb_raw = raw.get("fallback", {})
        fallback_cfg = FallbackConfig(
            enabled=fb_raw.get("enabled", True),
            chain=_parse_list(
                os.environ.get("VIBE_FALLBACK_CHAIN"),
                fb_raw.get("chain", [llm_cfg.default_model]),
            ),
            circuit_breaker_threshold=_parse_int(
                "VIBE_CB_THRESHOLD",
                int(fb_raw.get("circuit_breaker_threshold", 5)),
            ),
            circuit_breaker_cooldown=_parse_float(
                "VIBE_CB_COOLDOWN",
                float(fb_raw.get("circuit_breaker_cooldown", 60.0)),
            ),
        )

        # ---- SecurityConfig ----
        sec_raw = raw.get("security", {})
        security_cfg = _parse_security_config(sec_raw)

        # ---- EvalConfig ----
        eval_raw = raw.get("eval", {})
        eval_cfg = EvalConfig(
            scorecard_dir=os.path.expanduser(
                eval_raw.get("scorecard_dir", "~/.vibe/scorecards")
            ),
        )

        # ---- Providers ----
        providers_raw = raw.get("providers", {})
        providers_reg = _parse_providers(providers_raw, llm_cfg)

        # ---- Models section ----
        models_dict = raw.get("models", {})

        # ---- Build VibeConfig ----
        cfg = cls.__new__(cls)
        # Use model_construct to bypass pydantic-settings env reading
        cfg = cls.model_construct(
            debug=raw.get("debug", False),
            log_level=(raw.get("log_level", "INFO") or "INFO").upper(),
            workspace_dir=raw.get("workspace_dir", "./workspace"),
            llm=llm_cfg,
            logging=LogConfig(),
            model=ModelConfig(),
            planner=PlannerConfig(),
            trace_store=TraceStoreConfig(),
            eval=eval_cfg,
            security=security_cfg,
            memory=TripartiteMemoryConfig(),
            fallback=fallback_cfg,
            providers=providers_reg,
            models=models_dict,
            resolved_model=None,
            api_key=None,
            base_url=None,
        )
        return cfg

    # ------------------------------------------------------------------
    # Legacy pydantic-settings load() fallback (no args)
    # ------------------------------------------------------------------

    def get_fallback_chain(self) -> list[str]:
        """Get the ordered fallback model chain.

        When fallback.enabled is False, returns only the default model.
        Always inserts default_model at position 0 if missing.
        """
        if not self.fallback.enabled:
            return [self.llm.default_model]
        chain = list(self.fallback.chain)
        if not chain:
            chain = [self.llm.default_model]
        if self.llm.default_model not in chain:
            chain.insert(0, self.llm.default_model)
        return chain

    def get_security_config(self) -> SecurityConfig:
        """Return the active SecurityConfig, applying VIBE_APPROVAL_MODE env override."""
        sec = self.security
        override = os.environ.get("VIBE_APPROVAL_MODE")
        if override and override in ("manual", "smart", "auto"):
            sec = sec.model_copy(update={"approval_mode": override})
        return sec

    def get_default_provider(self) -> Optional[ProviderProfile]:
        """Return the default provider profile, or None if no providers are registered."""
        if self.providers is None:
            return None
        names = self.providers.list_providers()
        if not names:
            return None
        return self.providers.get(names[0])

    def resolve_api_key(self) -> Optional[str]:
        """Resolve API key from various sources."""
        return (
            self.llm.api_key
            or self.api_key
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    def set_resolved_model(self, model: str) -> None:
        """Record the model that was selected after health-check resolution."""
        object.__setattr__(self, "resolved_model", model)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VibeConfig":
        """Load from a dictionary (new-style validation)."""
        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: str | Path) -> "VibeConfig":
        """Load from a YAML/JSON file (new-style convenience)."""
        return cls.load(path=path, auto_create=False)

    def to_dict(self) -> dict[str, Any]:
        """Export to dictionary."""
        return self.model_dump()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _default_yaml_dict() -> dict[str, Any]:
    """Build the default YAML dict written when auto_create=True."""
    return {
        "llm": {
            "default_model": "default",
            "base_url": "http://localhost:11434",
            "timeout": 60.0,
        },
        "fallback": {
            "enabled": True,
            "chain": ["default"],
            "circuit_breaker_threshold": 5,
            "circuit_breaker_cooldown": 60.0,
        },
    }


def _parse_security_config(raw: dict[str, Any]) -> SecurityConfig:
    """Build a SecurityConfig from a raw YAML dict, with env override support."""
    fs_raw = raw.get("file_safety", {})
    env_raw = raw.get("env_sanitization", {})
    sb_raw = raw.get("sandbox", {})
    audit_raw = raw.get("audit", {})

    file_safety = FileSafetyConfig(
        write_denylist_enabled=fs_raw.get("write_denylist_enabled", True),
        read_blocklist_enabled=fs_raw.get("read_blocklist_enabled", True),
        safe_root=fs_raw.get("safe_root"),
    )

    env_sanitization = EnvSanitizationConfig(
        enabled=env_raw.get("enabled", True),
        block_path_overrides=env_raw.get("block_path_overrides", True),
        strip_shell_env=env_raw.get("strip_shell_env", True),
        secret_prefixes=env_raw.get(
            "secret_prefixes",
            ["*_API_KEY", "*_TOKEN", "*_SECRET", "AWS_*", "GITHUB_*"],
        ),
    )

    sandbox = SandboxConfig(
        backend=sb_raw.get("backend", "local"),
        auto_approve_in_sandbox=sb_raw.get("auto_approve_in_sandbox", False),
    )

    audit = AuditConfig(
        log_path=audit_raw.get("log_path", os.path.expanduser("~/.vibe/logs/security.log")),
        max_events=int(audit_raw.get("max_events", 10000)),
        redact_in_logs=audit_raw.get("redact_in_logs", True),
    )

    # approval_mode: VIBE_APPROVAL_MODE env var overrides file value
    approval_mode = os.environ.get("VIBE_APPROVAL_MODE") or raw.get("approval_mode", "smart")

    return SecurityConfig(
        approval_mode=approval_mode,
        dangerous_patterns_enabled=raw.get("dangerous_patterns_enabled", True),
        secret_redaction=raw.get("secret_redaction", True),
        audit_logging=raw.get("audit_logging", True),
        fail_closed=raw.get("fail_closed", True),
        file_safety=file_safety,
        env_sanitization=env_sanitization,
        sandbox=sandbox,
        audit=audit,
    )

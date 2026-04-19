"""Vibe-agent independent configuration loader.

Decoupled from Hermes config. Loads from ~/.vibe/config.yaml with env overrides.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml import YAMLError


DEFAULT_CONFIG_PATH = Path.home() / ".vibe" / "config.yaml"

DEFAULT_CONFIG_CONTENT = """# Vibe Agent Configuration
# Independent from Hermes. Env vars override these values.
# Default endpoint targets Ollama (http://localhost:11434).
# Override VIBE_BASE_URL and VIBE_MODEL for other providers.

llm:
  default_model: "default"
  base_url: "http://localhost:11434"
  api_key_env_var: "LLM_API_KEY"
  timeout: 120.0

fallback:
  enabled: true
  chain:
    - "default"
  health_check_timeout: 10.0
  max_retries: 3

compactor:
  max_tokens: 8000
  chars_per_token: 4.0
  preserve_recent: 4
  max_chars_per_msg: 4000

query_loop:
  feedback_threshold: 0.7
  max_feedback_retries: 1
  max_iterations: 50
  max_context_tokens: 8000

retry:
  max_retries: 2
  initial_delay: 1.0

eval:
  default_cases_dir: "vibe/evals/builtin"
  scorecard_dir: "~/.vibe/scorecards"
  soak_default_duration_minutes: 60.0
  soak_default_cpm: 6.0
"""


@dataclass
class LLMConfig:
    default_model: str = "default"
    base_url: str = "http://localhost:11434"
    api_key_env_var: str = "LLM_API_KEY"
    api_key: str | None = None
    timeout: float = 120.0


@dataclass
class FallbackConfig:
    enabled: bool = True
    chain: list[str] = field(default_factory=lambda: ["default"])
    health_check_timeout: float = 10.0
    max_retries: int = 3


@dataclass
class CompactorConfig:
    max_tokens: int = 8000
    chars_per_token: float = 4.0
    preserve_recent: int = 4
    max_chars_per_msg: int = 4000

    def __post_init__(self):
        if self.max_tokens < 1000:
            raise ValueError(f"max_tokens must be >= 1000, got {self.max_tokens}")
        if self.chars_per_token <= 0:
            raise ValueError(f"chars_per_token must be > 0, got {self.chars_per_token}")
        if self.preserve_recent < 0:
            raise ValueError(f"preserve_recent must be >= 0, got {self.preserve_recent}")
        if self.max_chars_per_msg < 100:
            raise ValueError(f"max_chars_per_msg must be >= 100, got {self.max_chars_per_msg}")


@dataclass
class QueryLoopConfig:
    feedback_threshold: float = 0.7
    max_feedback_retries: int = 1
    max_iterations: int = 50
    max_context_tokens: int = 8000

    def __post_init__(self):
        if not 0.0 <= self.feedback_threshold <= 1.0:
            raise ValueError(
                f"feedback_threshold must be in [0.0, 1.0], got {self.feedback_threshold}"
            )
        if self.max_feedback_retries < 0:
            raise ValueError(
                f"max_feedback_retries must be >= 0, got {self.max_feedback_retries}"
            )
        if self.max_iterations < 1:
            raise ValueError(f"max_iterations must be >= 1, got {self.max_iterations}")
        if self.max_context_tokens < 1000:
            raise ValueError(
                f"max_context_tokens must be >= 1000, got {self.max_context_tokens}"
            )


@dataclass
class RetryConfig:
    max_retries: int = 2
    initial_delay: float = 1.0

    def __post_init__(self):
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")
        if self.initial_delay < 0:
            raise ValueError(f"initial_delay must be >= 0, got {self.initial_delay}")


@dataclass
class EvalConfig:
    default_cases_dir: str = "vibe/evals/builtin"
    scorecard_dir: str = "~/.vibe/scorecards"
    soak_default_duration_minutes: float = 60.0
    soak_default_cpm: float = 6.0

    def __post_init__(self):
        # Expand ~ in paths
        self.scorecard_dir = os.path.expanduser(self.scorecard_dir)


@dataclass
class VibeConfig:
    """Top-level configuration for vibe-agent."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    compactor: CompactorConfig = field(default_factory=CompactorConfig)
    query_loop: QueryLoopConfig = field(default_factory=QueryLoopConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)

    # Track the actual model resolved after health-check fallback
    resolved_model: str | None = None

    def set_resolved_model(self, model: str) -> None:
        """Record the actual model used after fallback resolution."""
        self.resolved_model = model

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        auto_create: bool = True,
    ) -> "VibeConfig":
        """Load config from file, apply env overrides, return VibeConfig."""
        config_path = path or DEFAULT_CONFIG_PATH

        if auto_create and not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")

        raw: dict[str, Any] = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                try:
                    loaded = yaml.safe_load(f)
                except YAMLError as exc:
                    raise ValueError(
                        f"Invalid YAML in config file {config_path}: {exc}"
                    ) from exc

            if loaded is None:
                raw = {}
            elif not isinstance(loaded, dict):
                raise ValueError(
                    f"Config file {config_path} must contain a top-level mapping, "
                    f"got {type(loaded).__name__}"
                )
            else:
                raw = loaded

        # Build from file + env overrides
        llm_raw = raw.get("llm", {})
        llm = LLMConfig(
            default_model=os.getenv("VIBE_MODEL", llm_raw.get("default_model", "default")),
            base_url=os.getenv("VIBE_BASE_URL", llm_raw.get("base_url", "http://localhost:11434")),
            api_key_env_var=os.getenv(
                "VIBE_API_KEY_ENV_VAR", llm_raw.get("api_key_env_var", "LLM_API_KEY")
            ),
            api_key=llm_raw.get("api_key"),
            timeout=_parse_float("VIBE_TIMEOUT", llm_raw.get("timeout", 120.0)),
        )

        fb_raw = raw.get("fallback", {})
        fallback = FallbackConfig(
            enabled=_parse_bool(
                os.getenv("VIBE_FALLBACK_ENABLED", str(fb_raw.get("enabled", True)))
            ),
            chain=_parse_list(os.getenv("VIBE_FALLBACK_CHAIN"), fb_raw.get("chain")),
            health_check_timeout=_parse_float(
                "VIBE_HEALTH_TIMEOUT", fb_raw.get("health_check_timeout", 10.0)
            ),
            max_retries=_parse_int("VIBE_FALLBACK_RETRIES", fb_raw.get("max_retries", 3)),
        )

        comp_raw = raw.get("compactor", {})
        compactor = CompactorConfig(
            max_tokens=_parse_int("VIBE_COMPACTOR_MAX_TOKENS", comp_raw.get("max_tokens", 8000)),
            chars_per_token=_parse_float(
                "VIBE_COMPACTOR_CHARS_PER_TOKEN", comp_raw.get("chars_per_token", 4.0)
            ),
            preserve_recent=_parse_int(
                "VIBE_COMPACTOR_PRESERVE_RECENT", comp_raw.get("preserve_recent", 4)
            ),
            max_chars_per_msg=_parse_int(
                "VIBE_COMPACTOR_MAX_CHARS", comp_raw.get("max_chars_per_msg", 4000)
            ),
        )

        ql_raw = raw.get("query_loop", {})
        query_loop = QueryLoopConfig(
            feedback_threshold=_parse_float(
                "VIBE_FEEDBACK_THRESHOLD", ql_raw.get("feedback_threshold", 0.7)
            ),
            max_feedback_retries=_parse_int(
                "VIBE_MAX_FEEDBACK_RETRIES", ql_raw.get("max_feedback_retries", 1)
            ),
            max_iterations=_parse_int(
                "VIBE_MAX_ITERATIONS", ql_raw.get("max_iterations", 50)
            ),
            max_context_tokens=_parse_int(
                "VIBE_MAX_CONTEXT_TOKENS", ql_raw.get("max_context_tokens", 8000)
            ),
        )

        retry_raw = raw.get("retry", {})
        retry = RetryConfig(
            max_retries=_parse_int("VIBE_RETRY_MAX_RETRIES", retry_raw.get("max_retries", 2)),
            initial_delay=_parse_float(
                "VIBE_RETRY_INITIAL_DELAY", retry_raw.get("initial_delay", 1.0)
            ),
        )

        eval_raw = raw.get("eval", {})
        eval_cfg = EvalConfig(
            default_cases_dir=os.getenv(
                "VIBE_EVAL_CASES_DIR", eval_raw.get("default_cases_dir", "vibe/evals/builtin")
            ),
            scorecard_dir=os.getenv(
                "VIBE_SCORECARD_DIR", eval_raw.get("scorecard_dir", "~/.vibe/scorecards")
            ),
            soak_default_duration_minutes=_parse_float(
                "VIBE_SOAK_DURATION", eval_raw.get("soak_default_duration_minutes", 60.0)
            ),
            soak_default_cpm=_parse_float(
                "VIBE_SOAK_CPM", eval_raw.get("soak_default_cpm", 6.0)
            ),
        )

        return cls(
            llm=llm,
            fallback=fallback,
            compactor=compactor,
            query_loop=query_loop,
            retry=retry,
            eval=eval_cfg,
        )

    def resolve_api_key(self) -> str | None:
        """Resolve API key: config file > env var > LLM_API_KEY fallback."""
        if self.llm.api_key:
            return self.llm.api_key
        return os.getenv(self.llm.api_key_env_var) or os.getenv("LLM_API_KEY")

    def get_fallback_chain(self) -> list[str]:
        """Return the ordered fallback chain from config."""
        if not self.fallback.enabled:
            return [self.llm.default_model]
        chain = list(self.fallback.chain)
        # Ensure default model is first if not already in chain
        if self.llm.default_model not in chain:
            chain.insert(0, self.llm.default_model)
        return chain


def _parse_bool(value: str) -> bool:
    lowered = value.lower()
    if lowered in ("true", "1", "yes", "on"):
        return True
    if lowered in ("false", "0", "no", "off", ""):
        return False
    raise ValueError(f"Cannot parse '{value}' as boolean. Expected: true/false/1/0/yes/no/on/off")


def _parse_float(env_name: str, fallback: float) -> float:
    env_val = os.getenv(env_name)
    if env_val is None:
        return fallback
    try:
        return float(env_val)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {env_name} must be a float, got '{env_val}'"
        ) from exc


def _parse_int(env_name: str, fallback: int) -> int:
    env_val = os.getenv(env_name)
    if env_val is None:
        return fallback
    try:
        return int(env_val)
    except ValueError as exc:
        raise ValueError(
            f"Environment variable {env_name} must be an int, got '{env_val}'"
        ) from exc


def _parse_list(env_value: str | None, fallback: list[str] | None) -> list[str]:
    if env_value:
        return [x.strip() for x in env_value.split(",") if x.strip()]
    if fallback is not None:
        if not isinstance(fallback, list) or not all(isinstance(x, str) for x in fallback):
            raise ValueError(f"Expected list of strings, got {type(fallback).__name__}")
        return list(fallback)
    return []

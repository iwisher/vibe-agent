"""Vibe-agent independent configuration loader.

Decoupled from Hermes config. Loads from ~/.vibe/config.yaml with env overrides.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from yaml import YAMLError


DEFAULT_CONFIG_PATH = Path.home() / ".vibe" / "config.yaml"

DEFAULT_CONFIG_CONTENT = """# Vibe Agent Configuration
# Independent from Hermes. Env vars override these values.

llm:
  default_model: "qwen3.5-plus"
  base_url: "http://ai-api.applesay.cn"
  api_key_env_var: "APPLEsay_API_KEY"
  timeout: 120.0

fallback:
  enabled: true
  chain:
    - "qwen3.5-plus"
    - "kimi-k2.5"
    - "glm-5"
    - "minimax-m2.7"
    - "minimax-m2.5"
  health_check_timeout: 10.0
  max_retries: 3

eval:
  default_cases_dir: "vibe/evals/builtin"
  scorecard_dir: "~/.vibe/scorecards"
  soak_default_duration_minutes: 60.0
  soak_default_cpm: 6.0
"""


@dataclass
class LLMConfig:
    default_model: str = "qwen3.5-plus"
    base_url: str = "http://ai-api.applesay.cn"
    api_key_env_var: str = "APPLEsay_API_KEY"
    api_key: Optional[str] = None
    timeout: float = 120.0


@dataclass
class FallbackConfig:
    enabled: bool = True
    chain: List[str] = field(default_factory=lambda: [
        "qwen3.5-plus",
        "kimi-k2.5",
        "glm-5",
        "minimax-m2.7",
        "minimax-m2.5",
    ])
    health_check_timeout: float = 10.0
    max_retries: int = 3


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
    eval: EvalConfig = field(default_factory=EvalConfig)

    # Track the actual model resolved after health-check fallback
    resolved_model: Optional[str] = None

    def set_resolved_model(self, model: str) -> None:
        """Record the actual model used after fallback resolution."""
        self.resolved_model = model

    @classmethod
    def load(
        cls,
        path: Optional[Path] = None,
        auto_create: bool = True,
    ) -> "VibeConfig":
        """Load config from file, apply env overrides, return VibeConfig."""
        config_path = path or DEFAULT_CONFIG_PATH

        if auto_create and not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")

        raw: Dict[str, Any] = {}
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
            default_model=os.getenv("VIBE_MODEL", llm_raw.get("default_model", "qwen3.5-plus")),
            base_url=os.getenv("VIBE_BASE_URL", llm_raw.get("base_url", "http://ai-api.applesay.cn")),
            api_key_env_var=os.getenv(
                "VIBE_API_KEY_ENV_VAR", llm_raw.get("api_key_env_var", "APPLEsay_API_KEY")
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

        return cls(llm=llm, fallback=fallback, eval=eval_cfg)

    def resolve_api_key(self) -> Optional[str]:
        """Resolve API key: config file > env var > LLM_API_KEY fallback."""
        if self.llm.api_key:
            return self.llm.api_key
        return os.getenv(self.llm.api_key_env_var) or os.getenv("LLM_API_KEY")

    def get_fallback_chain(self) -> List[str]:
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


def _parse_list(env_value: Optional[str], fallback: Optional[List[str]]) -> List[str]:
    if env_value:
        return [x.strip() for x in env_value.split(",") if x.strip()]
    if fallback is not None:
        if not isinstance(fallback, list) or not all(isinstance(x, str) for x in fallback):
            raise ValueError(f"Expected list of strings, got {type(fallback).__name__}")
        return list(fallback)
    return []

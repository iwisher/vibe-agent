"""Live-tier (Tier C) target config tests — construction only, no network."""

from vibe.redteam.live import (
    GEMINI_BASE_URL,
    GEMINI_DEFAULT_MODEL,
    KIMI_CODE_BASE_URL,
    LIVE_PROVIDERS,
    gemini_config,
    kimi_code_config,
)


def test_kimi_config_defaults(monkeypatch):
    monkeypatch.delenv("VIBE_API_KEY", raising=False)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    cfg = kimi_code_config()
    assert cfg.provider == "kimi"
    assert cfg.base_url == KIMI_CODE_BASE_URL
    assert cfg.model == "k3"
    assert cfg.api_key == ""


def test_kimi_config_env_key(monkeypatch):
    # Only the endpoint-specific key is honored; the generic VIBE_API_KEY must
    # never be sent to the Kimi endpoint (cross-provider credential leak).
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.setenv("VIBE_API_KEY", "vibe-key")
    assert kimi_code_config().api_key == "kimi-key"
    monkeypatch.delenv("KIMI_API_KEY")
    assert kimi_code_config().api_key == ""


def test_gemini_config_defaults(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    cfg = gemini_config()
    assert cfg.provider == "gemini"
    assert cfg.base_url == GEMINI_BASE_URL
    assert cfg.model == GEMINI_DEFAULT_MODEL
    assert cfg.api_key == ""


def test_gemini_config_env_key_precedence(monkeypatch):
    # Both vars are Gemini-specific, so either is safe; GEMINI_ wins.
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-key")
    assert gemini_config().api_key == "gem-key"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert gemini_config().api_key == "goog-key"
    # The generic VIBE key must never reach the Gemini endpoint either.
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.setenv("VIBE_API_KEY", "vibe-key")
    assert gemini_config().api_key == ""


def test_whitespace_key_treated_as_missing(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "   ")
    assert kimi_code_config().api_key == ""
    monkeypatch.setenv("GEMINI_API_KEY", "\t ")
    assert gemini_config().api_key == ""


def test_gemini_whitespace_key_falls_back_to_google(monkeypatch):
    # A whitespace GEMINI_API_KEY must not shadow a valid GOOGLE_API_KEY.
    monkeypatch.setenv("GEMINI_API_KEY", "  ")
    monkeypatch.setenv("GOOGLE_API_KEY", "goog-key")
    assert gemini_config().api_key == "goog-key"


def test_gemini_config_model_override():
    assert gemini_config(model="gemini-2.5-pro").model == "gemini-2.5-pro"


def test_api_key_never_leaks_via_repr(monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-secret")
    cfg = kimi_code_config()
    assert "sk-secret" not in repr(cfg)
    assert "sk-secret" not in str(cfg)


def test_kimi_config_model_override():
    assert kimi_code_config(model="kimi-for-coding").model == "kimi-for-coding"


def test_live_providers_registry():
    assert set(LIVE_PROVIDERS) == {"kimi", "gemini"}
    assert LIVE_PROVIDERS["gemini"]().provider == "gemini"


async def test_tier_c_probe_skips_without_api_key(monkeypatch):
    """The live probe must skip cleanly (no network, no failure) without a key."""
    for var in ("VIBE_API_KEY", "KIMI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    import scripts.run_redteam as runner

    for provider in ("kimi", "gemini"):
        result = await runner.run_tier_c_probe(provider)
        assert "skipped" in result, provider


async def test_tier_c_probe_unknown_provider_is_loud():
    import scripts.run_redteam as runner

    result = await runner.run_tier_c_probe("nonexistent")
    assert "error" in result
    assert "nonexistent" in result["error"]

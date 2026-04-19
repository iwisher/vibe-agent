"""Tests for adapter registry."""

import pytest
from vibe.adapters.registry import ADAPTER_REGISTRY, get_adapter
from vibe.adapters.openai import OpenAIAdapter
from vibe.adapters.anthropic import AnthropicAdapter


class TestAdapterRegistry:
    def test_openai_registered(self):
        assert "openai" in ADAPTER_REGISTRY
        assert ADAPTER_REGISTRY["openai"] is OpenAIAdapter

    def test_anthropic_registered(self):
        assert "anthropic" in ADAPTER_REGISTRY
        assert ADAPTER_REGISTRY["anthropic"] is AnthropicAdapter

    def test_get_adapter_success(self):
        cls = get_adapter("openai")
        assert cls is OpenAIAdapter

    def test_get_adapter_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown adapter"):
            get_adapter("nonexistent")

    def test_adapter_instances_are_independent(self):
        """Each get_adapter call returns the class, instances are separate."""
        a1 = get_adapter("openai")()
        a2 = get_adapter("openai")()
        assert isinstance(a1, OpenAIAdapter)
        assert isinstance(a2, OpenAIAdapter)
        assert a1 is not a2

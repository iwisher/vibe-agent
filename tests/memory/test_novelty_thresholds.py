"""Tests for per-tag novelty thresholds."""

from vibe.memory.novelty_thresholds import NoveltyThresholdRegistry


class TestNoveltyThresholdRegistry:
    def test_default_threshold(self):
        reg = NoveltyThresholdRegistry(default_threshold=0.5)
        assert reg.get_threshold() == 0.5
        assert reg.get_threshold(["unknown"]) == 0.5

    def test_set_and_get_threshold(self):
        reg = NoveltyThresholdRegistry(default_threshold=0.5)
        reg.set_threshold("finance", 0.8, "Strict for financial data")
        assert reg.get_threshold(["finance"]) == 0.8

    def test_most_strict_for_multiple_tags(self):
        reg = NoveltyThresholdRegistry(default_threshold=0.5)
        reg.set_threshold("finance", 0.8)
        reg.set_threshold("general", 0.3)
        # Should return the lowest (most strict)
        assert reg.get_threshold(["finance", "general"]) == 0.3

    def test_case_insensitive(self):
        reg = NoveltyThresholdRegistry()
        reg.set_threshold("Finance", 0.8)
        assert reg.get_threshold(["finance"]) == 0.8
        assert reg.get_threshold(["FINANCE"]) == 0.8

    def test_remove_threshold(self):
        reg = NoveltyThresholdRegistry()
        reg.set_threshold("test", 0.9)
        assert reg.remove_threshold("test") is True
        assert reg.get_threshold(["test"]) == 0.5  # Back to default
        assert reg.remove_threshold("test") is False

    def test_list_thresholds(self):
        reg = NoveltyThresholdRegistry()
        reg.set_threshold("b", 0.3)
        reg.set_threshold("a", 0.7)
        thresholds = reg.list_thresholds()
        assert len(thresholds) == 2
        assert thresholds[0].tag == "a"  # Sorted

    def test_is_novel(self):
        reg = NoveltyThresholdRegistry(default_threshold=0.5)
        reg.set_threshold("strict", 0.8)
        assert reg.is_novel(0.9) is True
        assert reg.is_novel(0.4) is False
        assert reg.is_novel(0.9, tags=["strict"]) is True
        assert reg.is_novel(0.7, tags=["strict"]) is False  # Below 0.8

    def test_from_config(self):
        class FakeConfig:
            novelty_threshold = 0.6
            tag_thresholds = {
                "finance": {"threshold": 0.9, "description": "Strict"},
                "general": 0.3,
            }

        reg = NoveltyThresholdRegistry.from_config(FakeConfig())
        assert reg.default_threshold == 0.6
        assert reg.get_threshold(["finance"]) == 0.9
        assert reg.get_threshold(["general"]) == 0.3

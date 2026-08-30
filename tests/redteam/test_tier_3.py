"""Tests for Tier 3 long-horizon challenged agent tasks."""

import pytest

from vibe.redteam.tier_3 import TIER_3_SCENARIOS, Tier3Scenario, run_tier_3_scenario
from vibe.redteam.victim import VictimHarness


def test_tier_3_scenario_catalog_count():
    assert len(TIER_3_SCENARIOS) == 10
    ids = [s.id for s in TIER_3_SCENARIOS]
    assert len(set(ids)) == 10
    for s in TIER_3_SCENARIOS:
        assert s.id.startswith("t3-")
        assert s.name
        assert s.category
        assert s.failure_mode


@pytest.mark.parametrize("scenario", TIER_3_SCENARIOS, ids=lambda s: s.id)
@pytest.mark.asyncio
async def test_tier_3_scenario_execution(scenario: Tier3Scenario):
    with VictimHarness() as victim:
        result = await run_tier_3_scenario(scenario, victim)
        assert result.passed is True, f"Scenario {scenario.id} failed: {result.detail}"
        assert result.scenario_id == scenario.id
        assert result.name == scenario.name
        assert result.category == scenario.category

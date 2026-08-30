"""Tier B tests: compromised-model scenarios against the full victim stack.

Each scenario runs a scripted hostile "model" through a real QueryLoop with the
real 5-layer security pipeline, inside the VictimHarness jail. Every attack must
be contained (denied by the expected layer, or refused by the tool itself) and
leave no side effects.
"""

import pytest

from vibe.redteam.tier_b import SCENARIOS, run_scenario
from vibe.redteam.victim import VictimHarness


async def test_scenario_catalog_shape():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    assert len(SCENARIOS) >= 5  # plan minimum
    assert all(s.severity in {"low", "medium", "high", "critical"} for s in SCENARIOS)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
async def test_compromised_model_contained(scenario):
    with VictimHarness() as victim:
        result = await run_scenario(scenario, victim)
    assert result.passed, (
        f"{scenario.id}: attack not contained as expected "
        f"(layer={result.layer}, side_effect_free={result.side_effect_free}, "
        f"detail={result.detail})"
    )
    assert result.side_effect_free
    if scenario.expected_layer is not None:
        assert result.layer == scenario.expected_layer

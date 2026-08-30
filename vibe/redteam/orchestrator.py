"""Red-team orchestrator (R0): runs corpus entries over pooled executor tasks.

Coordination rides the swarm ``EventBroker`` (plan §2): every executed entry
publishes a RESULT message so judge/reporter agents — and the dashboard — can
follow a run live. Execution itself is plain asyncio with a semaphore; the
swarm ``SubAgent`` task stub is intentionally not used for real work.
"""

import asyncio
import uuid

from vibe.redteam.corpus import CorpusEntry
from vibe.redteam.oracles import EXECUTORS, Finding, Observation, check_oracle
from vibe.swarm.protocol import AgentMessage, EventBroker, MessageType

TOPIC_REDTEAM = "redteam.results"


class RedTeamOrchestrator:
    """Runs the offline Tier-A attack matrix and collects findings."""

    def __init__(
        self,
        corpus: list[CorpusEntry],
        broker: EventBroker | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.corpus = corpus
        self.broker = broker or EventBroker()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_entry(self, entry: CorpusEntry) -> Finding:
        executor = EXECUTORS.get(entry.surface)
        if executor is None:
            observed = Observation(
                outcome="error", detail=f"no offline executor for {entry.surface}"
            )
        else:
            async with self._semaphore:
                try:
                    observed = await asyncio.to_thread(executor, entry)
                except Exception as e:  # executor bug — never confuse with a defense verdict
                    observed = Observation(
                        outcome="error", detail=f"executor raised {type(e).__name__}: {e}"
                    )
        finding = check_oracle(entry, observed)
        await self.broker.publish(
            AgentMessage(
                msg_type=MessageType.RESULT,
                sender="redteam-executor",
                recipient=None,
                content=f"{entry.id}: {'PASS' if finding.passed else 'FAIL'} ({observed.outcome})",
                correlation_id=entry.id,
                metadata={"surface": entry.surface, "passed": finding.passed},
            ),
            topics=[TOPIC_REDTEAM],
        )
        return finding

    async def run(self, surfaces: set[str] | None = None) -> list[Finding]:
        """Execute corpus entries (optionally filtered by surface) concurrently."""
        selected = [e for e in self.corpus if surfaces is None or e.surface in surfaces]
        run_id = uuid.uuid4().hex[:8]
        await self.broker.publish(
            AgentMessage(
                msg_type=MessageType.BROADCAST,
                sender="redteam-orchestrator",
                recipient=None,
                content=f"run {run_id}: {len(selected)} attacks queued",
                correlation_id=run_id,
            ),
            topics=[TOPIC_REDTEAM],
        )
        return await asyncio.gather(*(self._run_entry(e) for e in selected))

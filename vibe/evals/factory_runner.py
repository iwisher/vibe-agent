"""Factory-per-case EvalRunner — eliminates state bleed between eval cases.

Reuses the existing EvalRunner but enforces fresh QueryLoop creation
per case via a factory function, preventing state contamination.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from vibe.core.query_loop import QueryLoop
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalCase, EvalResult

QueryLoopFactory = Callable[[EvalCase], QueryLoop]


class FactoryEvalRunner:
    """EvalRunner that creates a fresh QueryLoop per case via factory.

    This eliminates state bleed between eval cases by ensuring each case
    gets its own isolated QueryLoop instance.
    """

    def __init__(
        self,
        factory: QueryLoopFactory,
        max_concurrency: int = 3,
    ) -> None:
        self.factory = factory
        self.max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def run_case(self, case: EvalCase) -> EvalResult:
        """Run a single eval case with a fresh QueryLoop."""
        async with self._semaphore:
            loop = self.factory(case)
            try:
                runner = EvalRunner(query_loop=loop)
                return await runner.run_case(case, query_loop=loop)
            finally:
                await loop.close()

    async def run_all(self, cases: list[EvalCase]) -> list[EvalResult]:
        """Run all eval cases concurrently with isolated QueryLoops."""
        tasks = [self.run_case(c) for c in cases]
        return await asyncio.gather(*tasks)

    def run_all_sync(self, cases: list[EvalCase]) -> list[EvalResult]:
        """Synchronous wrapper for run_all."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.run_all(cases))
        finally:
            loop.close()

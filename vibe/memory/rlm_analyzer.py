"""RLM Threshold Analyzer — Phase 2 telemetry-triggered RLM activation.

Analyzes TelemetryCollector data to decide when to trigger RLM (Recursive
Language Model) training. Phase 2 MVP: only logs the decision.
Actual training deferred to Phase 3.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RLMTriggerDecision:
    """Decision from RLM threshold analysis."""

    should_trigger: bool
    reason: str
    metrics: dict


class RLMThresholdAnalyzer:
    """Analyze telemetry and decide if RLM training should trigger.

    Phase 2 MVP: Only computes the decision. Does NOT perform training.
    """

    def __init__(self, telemetry: Any, config: Any) -> None:
        self.telemetry = telemetry
        self.config = config

    async def analyze(self) -> RLMTriggerDecision:
        """Analyze recent telemetry and return trigger decision.

        Returns RLMTriggerDecision with should_trigger=True if metrics
        cross configured thresholds.
        """
        try:
            window = getattr(self.config, "trigger_window_sessions", 50)
            min_sessions = getattr(self.config, "min_sessions_before_trigger", 10)
            threshold_chars = getattr(self.config, "trigger_threshold_chars", 100_000)
            threshold_compaction_pct = getattr(
                self.config, "trigger_threshold_compaction_pct", 0.3
            )

            # Query telemetry for recent session stats
            stats = await self._query_session_stats(window)

            total_sessions = stats.get("total_sessions", 0)
            if total_sessions < min_sessions:
                return RLMTriggerDecision(
                    should_trigger=False,
                    reason=f"Insufficient sessions: {total_sessions} < {min_sessions}",
                    metrics=stats,
                )

            # Check thresholds
            chars_violations = stats.get("sessions_above_char_threshold", 0)
            compaction_pct = stats.get("compaction_session_pct", 0.0)

            reasons: list[str] = []
            if chars_violations > 0:
                reasons.append(
                    f"{chars_violations} sessions exceeded {threshold_chars} chars"
                )
            if compaction_pct >= threshold_compaction_pct:
                reasons.append(f"{compaction_pct:.1%} sessions had compaction (>= {threshold_compaction_pct:.0%})")

            if reasons:
                return RLMTriggerDecision(
                    should_trigger=True,
                    reason="; ".join(reasons),
                    metrics=stats,
                )

            return RLMTriggerDecision(
                should_trigger=False,
                reason="All metrics within thresholds",
                metrics=stats,
            )
        except Exception as e:
            logger.warning("RLM analysis failed (non-fatal): %s", e)
            return RLMTriggerDecision(
                should_trigger=False,
                reason=f"Analysis error: {e}",
                metrics={},
            )

    async def analyze_and_train(
        self,
        wiki: Any,
        trace_store: Any,
        rlm_trainer: Any,
        rlm_config: Any
    ) -> RLMTriggerDecision:
        """Analyze telemetry and optionally trigger training in the background.

        If analysis says should_trigger and auto_train is true, launches training
        via the RLMTrainer without blocking.
        """
        decision = await self.analyze()

        if decision.should_trigger:
            auto_train = getattr(rlm_config, "auto_train", False)
            if auto_train:
                from vibe.memory.rlm_trainer import RLMTrainingConfig

                logger.info(f"RLM Training triggered. Reason: {decision.reason}")

                try:
                    import tempfile
                    from pathlib import Path

                    # Create a training config from rlm_config
                    dataset_path = Path(tempfile.gettempdir()) / "vibe_rlm_dataset.jsonl"
                    output_path = getattr(rlm_config, "rlm_model_path", None)
                    if not output_path:
                        output_path = str(Path.home() / ".vibe" / "models" / "rlm-adapter")

                    train_config = RLMTrainingConfig(
                        base_model=getattr(rlm_config, "base_model", "qwen3:1.7b"),
                        output_path=output_path,
                        dataset_path=str(dataset_path),
                        max_steps=getattr(rlm_config, "max_train_steps", 100),
                        lora_r=getattr(rlm_config, "lora_r", 8),
                        training_device=getattr(rlm_config, "training_device", "auto"),
                        ollama_register=getattr(rlm_config, "ollama_register", True),
                    )

                    async def _background_train():
                        await rlm_trainer.prepare_dataset(wiki, trace_store, dataset_path)
                        await rlm_trainer.train(train_config)

                    import asyncio
                    asyncio.create_task(_background_train())

                except Exception as e:
                    logger.error(f"Failed to launch RLM training: {e}")
            else:
                logger.info(f"RLM Training triggered (log only, auto_train=False). Reason: {decision.reason}")

        return decision

    async def _query_session_stats(self, window: int) -> dict:
        """Query telemetry DB for session statistics.

        Returns dict with:
        - total_sessions: int
        - sessions_above_char_threshold: int
        - compaction_session_pct: float
        - avg_duration_seconds: float
        """
        stats = {
            "total_sessions": 0,
            "sessions_above_char_threshold": 0,
            "compaction_session_pct": 0.0,
            "avg_duration_seconds": 0.0,
        }

        if self.telemetry is None or self.telemetry.db is None:
            return stats

        try:
            db = self.telemetry.db
            threshold_chars = getattr(self.config, "trigger_threshold_chars", 100_000)

            # Query recent sessions
            # Schema: _telemetry table has (type, session_id, timestamp, data_json)
            cursor = db.conn.execute(
                """
                SELECT data_json FROM _telemetry
                WHERE type = 'session'
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (window,),
            )
            rows = cursor.fetchall()

            if not rows:
                return stats

            total_chars_list: list[int] = []
            total_duration = 0.0
            session_ids: set[str] = set()

            for row in rows:
                try:
                    data = json.loads(row[0])
                    total_chars_list.append(data.get("total_chars", 0))
                    total_duration += data.get("duration_seconds", 0.0)
                    session_id = data.get("session_id")
                    if session_id:
                        session_ids.add(session_id)
                except (json.JSONDecodeError, TypeError):
                    continue

            stats["total_sessions"] = len(rows)
            stats["sessions_above_char_threshold"] = sum(
                1 for c in total_chars_list if c >= threshold_chars
            )
            stats["avg_duration_seconds"] = (
                total_duration / len(rows) if rows else 0.0
            )

            # Query compaction events for the same sessions
            if session_ids:
                placeholders = ",".join("?" * len(session_ids))
                cursor = db.conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT session_id) FROM _telemetry
                    WHERE type = 'compaction' AND session_id IN ({placeholders})
                    """,
                    tuple(session_ids),
                )
                compaction_sessions = cursor.fetchone()[0] or 0
                stats["compaction_session_pct"] = (
                    compaction_sessions / len(session_ids) if session_ids else 0.0
                )

        except Exception as e:
            logger.debug("Telemetry query failed: %s", e)

        return stats

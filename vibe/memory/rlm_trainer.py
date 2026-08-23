"""RLM Training Orchestrator.

Handles data preparation and subprocess invocation for LoRA fine-tuning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# AgentHER-style relabeling prompt (D2): for each failed session, ONE LLM call
# proposes either an achievable alternative goal the trajectory actually
# demonstrates, or discard.
_RELABEL_PROMPT_TEMPLATE = """You are a data relabeling engine for agent training data.

The agent session below FAILED to achieve its original goal. Your job: decide \
whether the trajectory still demonstrates some other coherent, achievable goal \
that was in fact accomplished (hindsight relabeling), and if so, state that \
alternative goal.

Rules:
- The alternative goal must be fully demonstrated by the trajectory's final \
outcome — never a goal the agent only partially approached.
- Keep the alternative goal close to the original intent when possible.
- If the trajectory is incoherent, empty, or demonstrates nothing useful, discard it.
- confidence is a float 0.0-1.0: how sure you are the final outcome fully \
achieves the alternative goal.

ORIGINAL GOAL:
{goal}

TRAJECTORY:
{transcript}

Respond with ONLY a JSON object. No markdown code fences, no extra text.
Either: {{"discard": true}}
Or: {{"discard": false, "achievable_goal": "...", "confidence": 0.0, "rationale": "..."}}
"""

_RELABEL_MAX_TRANSCRIPT_CHARS = 8000
_RELABEL_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class RLMTrainingConfig:
    """Configuration for an RLM training run."""

    base_model: str
    output_path: str
    dataset_path: str
    hf_model_id: str | None = None
    max_steps: int = 100
    lora_r: int = 8
    training_device: str = "auto"
    ollama_register: bool = True

    def to_dict(self) -> dict:
        return {
            "base_model": self.base_model,
            "output_path": self.output_path,
            "dataset_path": self.dataset_path,
            "hf_model_id": self.hf_model_id,
            "max_steps": self.max_steps,
            "lora_r": self.lora_r,
            "training_device": self.training_device,
        }


class RLMTrainer:
    """Orchestrates RLM training via a background subprocess.

    ``llm_client``/``rlm_config`` are optional; when both are present and
    ``rlm_config.relabel_failures`` is true, failed sessions are relabeled
    (AgentHER-style) during dataset export. Without them the export behaves
    exactly as before (successful sessions only).
    """

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        llm_client: Any | None = None,
        rlm_config: Any | None = None,
    ) -> None:
        self.ollama_base_url = ollama_base_url
        self.llm_client = llm_client
        self.rlm_config = rlm_config

    def _relabel_settings(self) -> tuple[bool, float]:
        """Return (enabled, min_confidence); disabled unless explicitly configured."""
        enabled = bool(
            self.llm_client is not None
            and self.rlm_config is not None
            and getattr(self.rlm_config, "relabel_failures", False)
        )
        try:
            min_confidence = float(getattr(self.rlm_config, "relabel_min_confidence", 0.7))
        except (TypeError, ValueError):
            min_confidence = 0.7
        return enabled, min_confidence

    async def prepare_dataset(self, wiki: Any, trace_store: Any, output_path: str | Path) -> Path:
        """Export wiki pages and trace sessions to a JSONL dataset.

        Format suitable for instruct tuning:
        {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        relabeled = 0
        discarded = 0
        relabel_enabled, relabel_min_confidence = self._relabel_settings()
        lines = []

        # 1. Export Wiki pages as factual QA
        try:
            pages = await wiki.list_pages(status="verified")
            for page in pages:
                # Simple QA generation from title to content
                record = {
                    "messages": [
                        {"role": "system", "content": "You are a helpful AI assistant."},
                        {"role": "user", "content": f"Tell me about {page.title}."},
                        {"role": "assistant", "content": page.content},
                    ]
                }
                lines.append(json.dumps(record))
                count += 1
        except Exception as e:
            logger.warning(f"Failed to export wiki pages for RLM: {e}")

        # 2. Export successful traces; optionally relabel failed ones (AgentHER)
        if trace_store:
            try:
                sessions = trace_store.get_recent_sessions(limit=100)
                for s in sessions:
                    if s.get("success"):
                        trace = trace_store.get_session_trace(s["id"])
                        if not trace or "steps" not in trace:
                            continue

                        # We just extract simple user/assistant turns
                        messages = [
                            {"role": "system", "content": "You are a helpful AI assistant."}
                        ]
                        valid = False

                        for step in trace["steps"]:
                            if step["type"] == "user":
                                messages.append({"role": "user", "content": step.get("text", "")})
                            elif step["type"] == "assistant":
                                messages.append(
                                    {"role": "assistant", "content": step.get("text", "")}
                                )
                                valid = True

                        if valid:
                            record = {"messages": messages}
                            lines.append(json.dumps(record))
                            count += 1
                        continue

                    # Failed session: one LLM call proposes an achievable
                    # alternative goal or discard. Never raises — a relabeling
                    # error simply excludes the session (previous behavior).
                    if not relabel_enabled:
                        continue
                    try:
                        record = await self._relabel_failed_session(
                            trace_store, s, relabel_min_confidence
                        )
                    except Exception as e:
                        logger.warning(
                            "RLM relabel failed for session %s (excluding): %s",
                            s.get("id"),
                            e,
                        )
                        record = None
                    if record is not None:
                        lines.append(json.dumps(record))
                        count += 1
                        relabeled += 1
                    else:
                        discarded += 1
            except Exception as e:
                logger.warning(f"Failed to export traces for RLM: {e}")

        if lines:
            await asyncio.to_thread(
                output_path.write_text, "\n".join(lines) + "\n", encoding="utf-8"
            )
        else:
            await asyncio.to_thread(output_path.write_text, "", encoding="utf-8")

        if relabel_enabled:
            logger.info(
                f"RLM relabeling: {relabeled} relabeled, {discarded} discarded "
                f"(min_confidence={relabel_min_confidence})"
            )
        logger.info(f"Exported {count} records to RLM dataset {output_path}")
        return output_path

    async def _relabel_failed_session(
        self, trace_store: Any, session: dict, min_confidence: float
    ) -> dict | None:
        """Propose an achievable alternative goal for a failed session.

        Returns the relabeled training record (marked ``relabeled: true`` with
        the original goal kept for provenance), or None to discard. Never
        raises — parse/LLM failures return None.
        """
        trace = trace_store.get_session_trace(session["id"])
        if not trace or "steps" not in trace:
            return None

        steps = trace["steps"]
        original_goal = ""
        last_assistant = ""
        transcript_lines = []
        for i, step in enumerate(steps):
            role = step.get("type", "?")
            text = step.get("text", "") or ""
            if role == "user" and not original_goal:
                original_goal = text
            if role == "assistant" and text.strip():
                last_assistant = text
            if role in ("user", "assistant") and text.strip():
                transcript_lines.append(f"[{i}] {role}: {text}")
        transcript = "\n".join(transcript_lines)[:_RELABEL_MAX_TRANSCRIPT_CHARS]
        if not transcript or not last_assistant:
            return None

        prompt = _RELABEL_PROMPT_TEMPLATE.format(goal=original_goal[:500], transcript=transcript)
        raw = await self._call_llm(prompt)
        if not raw:
            return None

        parsed = self._parse_relabel_response(raw)
        if parsed is None or parsed.get("discard"):
            return None

        achievable_goal = str(parsed.get("achievable_goal") or "").strip()
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if not achievable_goal or confidence < min_confidence:
            return None

        # The trajectory's final assistant turn is the demonstrated outcome of
        # the achievable goal; pair it with that goal as the training target.
        return {
            "messages": [
                {"role": "system", "content": "You are a helpful AI assistant."},
                {"role": "user", "content": achievable_goal},
                {"role": "assistant", "content": last_assistant},
            ],
            "relabeled": True,
            "original_goal": original_goal,
            "relabel_confidence": confidence,
            "session_id": session.get("id"),
        }

    @staticmethod
    def _parse_relabel_response(raw: str) -> dict | None:
        """Extract the JSON decision object from the relabel LLM response."""
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        match = _RELABEL_JSON_RE.search(text)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async def _call_llm(self, prompt: str) -> str | None:
        """Call the LLM with the relabel prompt. Returns raw response or None."""
        try:
            client = self.llm_client
            if client is None:
                return None
            if hasattr(client, "complete"):
                response = await client.complete(prompt)
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            if hasattr(client, "chat"):
                response = await client.chat([{"role": "user", "content": prompt}])
                if hasattr(response, "content"):
                    return response.content
                if isinstance(response, str):
                    return response
            logger.warning("LLM client has no compatible interface for relabeling")
            return None
        except Exception as e:
            logger.warning("LLM relabel call failed: %s", e)
            return None

    async def train(self, config: RLMTrainingConfig) -> Path | None:
        """Run LoRA fine-tuning via subprocess."""

        logger.info(f"Starting RLM training on {config.base_model} (max_steps={config.max_steps})")

        # We pass the config via stdin to the worker script
        config_json = json.dumps(config.to_dict())

        try:
            # The worker script must be executed in the same python environment
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "vibe.memory._rlm_train_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate(input=config_json.encode())

            if process.returncode == 0:
                logger.info(f"RLM training completed successfully: {config.output_path}")

                if config.ollama_register:
                    await self.register_with_ollama(config.output_path, f"{config.base_model}-rlm")

                return Path(config.output_path)
            else:
                logger.error(f"RLM training failed (exit {process.returncode}):\n{stderr.decode()}")
                return None

        except Exception as e:
            logger.error(f"Failed to launch RLM training subprocess: {e}")
            return None

    async def register_with_ollama(self, adapter_path: str, model_name: str) -> bool:
        """Register the fine-tuned adapter with Ollama."""
        try:
            # We would write a Modelfile pointing to the adapter, then call Ollama API
            # For Phase 3b MVP, we simulate the Ollama API call

            modelfile_content = f"""FROM {model_name.replace("-rlm", "")}
ADAPTER {adapter_path}
"""
            url = f"{self.ollama_base_url.rstrip('/')}/api/create"
            payload = {"name": model_name, "modelfile": modelfile_content}

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()

            logger.info(f"Registered RLM model with Ollama as {model_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to register RLM model with Ollama: {e}")
            return False

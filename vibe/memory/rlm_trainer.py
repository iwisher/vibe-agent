"""RLM Training Orchestrator.

Handles data preparation and subprocess invocation for LoRA fine-tuning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
    """Orchestrates RLM training via a background subprocess."""

    def __init__(self, ollama_base_url: str = "http://localhost:11434") -> None:
        self.ollama_base_url = ollama_base_url

    async def prepare_dataset(self, wiki: Any, trace_store: Any, output_path: str | Path) -> Path:
        """Export wiki pages and trace sessions to a JSONL dataset.

        Format suitable for instruct tuning:
        {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        count = 0
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
                        {"role": "assistant", "content": page.content}
                    ]
                }
                lines.append(json.dumps(record))
                count += 1
        except Exception as e:
            logger.warning(f"Failed to export wiki pages for RLM: {e}")

        # 2. Export successful traces
        if trace_store:
            try:
                sessions = trace_store.get_recent_sessions(limit=100)
                for s in sessions:
                    if not s.get("success"):
                        continue

                    trace = trace_store.get_session_trace(s["id"])
                    if not trace or "steps" not in trace:
                        continue

                    # We just extract simple user/assistant turns
                    messages = [{"role": "system", "content": "You are a helpful AI assistant."}]
                    valid = False

                    for step in trace["steps"]:
                        if step["type"] == "user":
                            messages.append({"role": "user", "content": step.get("text", "")})
                        elif step["type"] == "assistant":
                            messages.append({"role": "assistant", "content": step.get("text", "")})
                            valid = True

                    if valid:
                        record = {"messages": messages}
                        lines.append(json.dumps(record))
                        count += 1
            except Exception as e:
                logger.warning(f"Failed to export traces for RLM: {e}")

        if lines:
            await asyncio.to_thread(output_path.write_text, "\n".join(lines) + "\n", encoding="utf-8")
        else:
            await asyncio.to_thread(output_path.write_text, "", encoding="utf-8")

        logger.info(f"Exported {count} records to RLM dataset {output_path}")
        return output_path

    async def train(self, config: RLMTrainingConfig) -> Path | None:
        """Run LoRA fine-tuning via subprocess."""

        logger.info(f"Starting RLM training on {config.base_model} (max_steps={config.max_steps})")

        # We pass the config via stdin to the worker script
        config_json = json.dumps(config.to_dict())

        try:
            # The worker script must be executed in the same python environment
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "vibe.memory._rlm_train_worker",
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

            modelfile_content = f"""FROM {model_name.replace('-rlm', '')}
ADAPTER {adapter_path}
"""
            url = f"{self.ollama_base_url.rstrip('/')}/api/create"
            payload = {
                "name": model_name,
                "modelfile": modelfile_content
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()

            logger.info(f"Registered RLM model with Ollama as {model_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to register RLM model with Ollama: {e}")
            return False

#!/usr/bin/env python3
"""
End-to-end eval runner for vibe-agent.
Supports three modes:
  1. Standard eval: run all cases against one model
  2. Multi-model benchmark: compare models side-by-side
  3. Soak test: continuous loop for stress testing

Usage:
  python run_e2e_evals.py eval                    # Standard eval
  python run_e2e_evals.py benchmark               # Multi-model benchmark
  python run_e2e_evals.py soak --duration 60      # 60-minute soak test
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from vibe.core.config import VibeConfig
from vibe.core.model_gateway import LLMClient
from vibe.core.query_loop import QueryLoop
from vibe.core.context_compactor import ContextCompactor
from vibe.core.error_recovery import ErrorRecovery, RetryPolicy
from vibe.harness.constraints import HookPipeline, policy_hook, HookStage
from vibe.harness.memory.eval_store import EvalStore, EvalCase
from vibe.evals.runner import EvalRunner
from vibe.evals.model_registry import ModelRegistry, ModelProfile
from vibe.evals.multi_model_runner import MultiModelRunner
from vibe.evals.soak_test import SoakTestRunner, print_report
from vibe.evals.observability import Observability
from vibe.tools.tool_system import ToolSystem
from vibe.tools.bash import BashTool, BashSandbox
from vibe.tools.file import ReadFileTool, WriteFileTool
from vibe.tools.skill_manage import SkillManageTool

# ═══════════════════════════════════════════════════════════════════════════════
# Mock LLM for dry-run / CI
# ════════════════════════════════════════════════════════════════════════════════

class MockLLM:
    """Deterministic mock LLM for CI dry-runs. No API calls."""

    def __init__(self):
        self.call_count = 0
        self.model = "mock-llm"
        self.base_url = "http://localhost"
        self.api_key = "mock-key"

    async def chat(self, messages, tools=None, **kwargs):
        return await self.complete(messages, tools=tools, **kwargs)

    async def complete(self, messages, tools=None, **kwargs):
        self.call_count += 1
        last_msg = messages[-1]["content"] if messages else ""

        # Determine what tool to call based on prompt keywords
        tool_calls = []
        response_text = ""

        if "skill_manage" in last_msg.lower() or "create a skill" in last_msg.lower():
            tool_calls = [{
                "id": f"call_{self.call_count}",
                "type": "function",
                "function": {"name": "skill_manage", "arguments": json.dumps({
                    "action": "create",
                    "name": "test-e2e-skill",
                    "content": "This is a test skill created by the e2e eval suite.",
                })},
            }]
        elif "13 times 7" in last_msg or "math" in last_msg.lower():
            response_text = "The answer is 91."
        elif "bash" in last_msg.lower() or "echo" in last_msg.lower():
            if "sudo" in last_msg.lower():
                response_text = "The bash tool returned: blocked by security policy"
            else:
                tool_calls = [{
                    "id": f"call_{self.call_count}",
                    "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": "echo 'hello world'"})},
                }]
        elif "read" in last_msg.lower() or "file" in last_msg.lower():
            tool_calls = [{
                "id": f"call_{self.call_count}",
                "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "/tmp/test.txt"})},
            }]
        elif "write" in last_msg.lower():
            tool_calls = [{
                "id": f"call_{self.call_count}",
                "type": "function",
                "function": {"name": "write_file", "arguments": json.dumps({"path": "/tmp/test.txt", "content": "test"})},
            }]
        else:
            # Default response for reasoning/open-ended prompts
            response_text = (
                "The current market outlook is cautiously bullish. "
                "The S&P 500 has shown resilience despite macroeconomic headwinds. "
                "Key factors include recent CPI data and Fed policy expectations. "
                "For next week, I expect a sideways to slightly upward trend "
                "as earnings season continues to provide support. "
                "This market analysis is based on available economic indicators. "
                "Investors should watch for any sudden changes in market sentiment."
            )

        from vibe.core.query_loop import LLMResponse
        return LLMResponse(
            content=response_text,
            tool_calls=tool_calls,
            usage={"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
        )

    async def close(self):
        pass


def create_query_loop(base_url: str, model: str, api_key: str, vibe_config=None, dry_run: bool = False) -> QueryLoop:
    """Wire up a QueryLoop with real or mock LLM and real tools."""
    if dry_run:
        llm = MockLLM()
    else:
        from vibe.evals.model_registry import ModelRegistry
        registry = ModelRegistry()
        
        fallback_chain = []
        if vibe_config:
            for name in vibe_config.get_fallback_chain():
                profile = registry.get(name)
                model_id = profile.model_id if profile else name
                fallback_chain.append(model_id)
        
        llm = LLMClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout=120.0,
            retry_policy=RetryPolicy(max_retries=2, initial_delay=1.0),
            fallback_chain=fallback_chain,
            auto_fallback=True,
        )

    tool_system = ToolSystem()
    tool_system.register_tool(
        BashTool(sandbox=BashSandbox(working_dir=tempfile.gettempdir(), timeout=60))
    )
    tool_system.register_tool(ReadFileTool())
    tool_system.register_tool(WriteFileTool())
    tool_system.register_tool(SkillManageTool())

    hooks = HookPipeline()
    hooks.add_hook(HookStage.PRE_VALIDATE, policy_hook(blocked_commands=["curl | bash", "rm -rf /", "wget | bash", "sudo", "su -"]))

    return QueryLoop(
        llm_client=llm,
        tool_system=tool_system,
        context_compactor=ContextCompactor(max_tokens=16000),
        error_recovery=ErrorRecovery(RetryPolicy(max_retries=2, initial_delay=1.0)),
        hook_pipeline=hooks,
        max_iterations=15,
        max_context_tokens=16000,
    )


def load_config():
    """Load LLM config from VibeConfig (independent from Hermes)."""
    cfg = VibeConfig.load()
    api_key = cfg.resolve_api_key()
    base_url = cfg.llm.base_url
    model = cfg.llm.default_model

    # Env overrides still take highest priority
    api_key = os.getenv("LLM_API_KEY") or os.getenv("APPLEsay_API_KEY") or api_key
    base_url = os.getenv("LLM_BASE_URL", base_url)
    model = os.getenv("LLM_MODEL", model)

    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/").replace("/v1", ""),
        "model": model,
        "vibe_config": cfg,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Standard eval mode
# ════════════════════════════════════════════════════════════════════════════════

async def run_standard_eval(config: dict, cases: list, obs: Observability, dry_run: bool = False):
    query_loop = create_query_loop(
        config["base_url"], config["model"], config["api_key"],
        vibe_config=config.get("vibe_config"), dry_run=dry_run
    )
    eval_store = EvalStore()
    runner = EvalRunner(
        query_loop=query_loop, eval_store=eval_store, observability=obs
    )

    results = []
    start_total = time.time()

    for case in cases:
        print(f"\n{'─' * 70}")
        print(f"  ▶ RUNNING: {case.id}")
        print(f"  Prompt: {case.input.get('prompt', '')[:80]}...")
        print(f"{'─' * 70}")

        case_start = time.time()
        try:
            result = await runner.run_case(case)
        except Exception as e:
            result = EvalResult(
                eval_id=case.id,
                passed=False,
                diff={"exception": str(e)},
                total_tokens=0,
            )
            print(f"  [EXCEPTION] {e}")

        latency = time.time() - case_start
        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"  {status}  ({latency:.1f}s)")
        if not result.passed and result.diff:
            for k, v in result.diff.items():
                print(f"      diff → {k}: {v}")

        results.append(result)

    total_elapsed = time.time() - start_total
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print(f"\n{'═' * 70}")
    print("  RESULTS SUMMARY")
    print(f"{'═' * 70}")
    print(f"  Total cases : {len(results)}")
    print(f"  Passed      : {passed}  ({passed/len(results)*100:.1f}%)")
    print(f"  Failed      : {failed}  ({failed/len(results)*100:.1f}%)")
    print(f"  Total time  : {total_elapsed:.1f}s")
    print(f"{'═' * 70}")

    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"    {icon} {r.eval_id}")

    print(f"\n  DB summary: {eval_store.summary()}")

    await query_loop.llm.close()
    return 0 if failed == 0 else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Benchmark mode
# ═══════════════════════════════════════════════════════════════════════════════

async def run_benchmark(models: list, cases: list, parallel: bool, obs: Observability):
    registry = ModelRegistry()

    # Ensure API key is set for profiles that need one
    api_key = os.getenv("LLM_API_KEY") or os.getenv("APPLEsay_API_KEY")
    if api_key:
        for name in models:
            p = registry.get(name)
            if p and not p.api_key:
                p.api_key = api_key

    runner = MultiModelRunner(registry=registry, observability=obs)
    scorecard = await runner.run_all(model_names=models, cases=cases, parallel=parallel)
    runner.save_scorecard(scorecard)

    # Export observability
    exports = obs.export_all()
    print(f"\n[obs] Metrics: {exports['metrics']}")
    print(f"[obs] Trace: {exports['trace']}")

    return 0 if all(r.score == 1.0 for r in scorecard.models) else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Soak test mode
# ═══════════════════════════════════════════════════════════════════════════════

async def run_soak_test(
    config: dict,
    cases: list,
    duration_minutes: float,
    cases_per_minute: float,
    obs: Observability,
):
    def query_loop_factory():
        return create_query_loop(
            config["base_url"], config["model"], config["api_key"],
            vibe_config=config.get("vibe_config")
        )

    eval_store = EvalStore()
    soak = SoakTestRunner(
        query_loop_factory=query_loop_factory,
        eval_store=eval_store,
        model=config["model"],
        base_url=config["base_url"],
        duration_minutes=duration_minutes,
        cases_per_minute=cases_per_minute,
        observability=obs,
    )

    report = await soak.run(cases)
    print_report(report)

    # Export observability
    exports = obs.export_all()
    print(f"\n[obs] Metrics: {exports['metrics']}")
    print(f"[obs] Trace: {exports['trace']}")

    return 0 if report.pass_rate >= 0.95 else 1


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Agent End-to-End Eval Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  eval       Run standard eval suite against one model
  benchmark  Run multi-model comparison
  soak       Continuous stress test

Examples:
  python run_e2e_evals.py eval
  python run_e2e_evals.py benchmark --models default
  python run_e2e_evals.py soak --duration 60 --cpm 6
        """,
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    # Eval mode
    eval_parser = subparsers.add_parser("eval", help="Standard eval suite")
    eval_parser.add_argument("--model", help="Override model name")
    eval_parser.add_argument("--base-url", help="Override base URL")
    eval_parser.add_argument("--api-key", help="Override API key")
    eval_parser.add_argument(
        "--cases",
        help="Comma-separated case IDs to run (default: all)",
    )
    eval_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run with mock LLM (no API calls, for CI)",
    )

    # Benchmark mode
    bench_parser = subparsers.add_parser("benchmark", help="Multi-model benchmark")
    bench_parser.add_argument(
        "--models",
        default="default",
        help="Comma-separated model names from registry",
    )
    bench_parser.add_argument("--parallel", action="store_true", help="Run models in parallel")

    # Soak mode
    soak_parser = subparsers.add_parser("soak", help="Long-running soak test")
    soak_parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Duration in minutes (default: 60)",
    )
    soak_parser.add_argument(
        "--cpm",
        type=float,
        default=6.0,
        help="Cases per minute (default: 6)",
    )

    args = parser.parse_args()

    # Load config
    config = load_config()

    # Override from args
    if getattr(args, "model", None):
        config["model"] = args.model
    if getattr(args, "base_url", None):
        config["base_url"] = args.base_url
    if getattr(args, "api_key", None):
        config["api_key"] = args.api_key

    if not config["api_key"] and not getattr(args, "dry_run", False):
        print("\n[ERROR] No API key found. Set LLM_API_KEY env var, or use --dry-run.")
        sys.exit(1)

    if config["api_key"]:
        os.environ["LLM_API_KEY"] = config["api_key"]

    # Load eval cases
    evals_dir = Path(__file__).parent / "vibe" / "evals" / "builtin"
    store = EvalStore(evals_dir=str(evals_dir))
    cases = store.load_builtin_evals()

    if not cases:
        print(f"\n[ERROR] No eval cases found in {evals_dir}")
        sys.exit(1)

    # Filter cases if --cases specified
    if getattr(args, "cases", None):
        wanted = {c.strip() for c in args.cases.split(",")}
        cases = [c for c in cases if c.id in wanted]
        if not cases:
            print(f"\n[ERROR] No matching cases found for IDs: {wanted}")
            sys.exit(1)

    # Observability
    obs = Observability()

    # Dispatch
    if args.mode == "eval":
        exit_code = asyncio.run(run_standard_eval(config, cases, obs, dry_run=getattr(args, "dry_run", False)))
    elif args.mode == "benchmark":
        models = [m.strip() for m in args.models.split(",")]
        exit_code = asyncio.run(run_benchmark(models, cases, args.parallel, obs))
    elif args.mode == "soak":
        exit_code = asyncio.run(
            run_soak_test(config, cases, args.duration, args.cpm, obs)
        )
    else:
        parser.print_help()
        sys.exit(1)

    # Export observability data
    exports = obs.export_all()
    print(f"\n[obs] Metrics exported: {exports['metrics']}")
    print(f"[obs] Trace exported: {exports['trace']}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

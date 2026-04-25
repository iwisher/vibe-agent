"""Main CLI entry point for Vibe Agent."""

import asyncio
import readline
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vibe.core.config import VibeConfig
from vibe.core.query_loop import QueryLoop
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.core.logger import setup_session_logger
from vibe.evals.model_registry import ModelRegistry
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalStore
from vibe.harness.memory.trace_store import TraceStore
from vibe.cli.skill_commands import app as skill_app

app = typer.Typer(help="Vibe Agent — an open agent harness platform")
eval_app = typer.Typer(help="Run and manage evals")
app.add_typer(eval_app, name="eval")
memory_app = typer.Typer(help="Inspect stored traces and eval results")
app.add_typer(memory_app, name="memory")
app.add_typer(skill_app, name="skill")
console = Console()

DEFAULT_CONFIG = VibeConfig.load()

# Persistent history file for interactive mode
_HISTORY_FILE = Path.home() / ".vibe" / "history"


def _setup_readline_history() -> None:
    """Enable readline with persistent history file."""
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _HISTORY_FILE.exists():
            readline.read_history_file(str(_HISTORY_FILE))
        readline.set_history_length(1000)
    except Exception:
        # readline may not be available on all platforms
        pass


def _save_readline_history() -> None:
    """Save readline history to disk."""
    try:
        readline.write_history_file(str(_HISTORY_FILE))
    except Exception:
        pass


async def interactive_mode(query_loop: QueryLoop) -> None:
    _setup_readline_history()
    console.print("[bold green]Vibe Agent[/bold green] ready. Type /exit to quit, /clear to reset.")
    while True:
        try:
            # Use built-in input() with readline for arrow-key history support
            # Rich console.input() doesn't process terminal escape sequences
            console.print("[bold cyan]>[/bold cyan] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            _save_readline_history()
            console.print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "exit", "quit"):
            _save_readline_history()
            console.print("Goodbye!")
            break
        if user_input.lower() == "/clear":
            query_loop.clear_history()
            console.print("History cleared.")
            continue

        query_loop.add_user_message(user_input)
        async for result in query_loop.run():
            if result.error:
                console.print(Panel(str(result.error), title="Error", border_style="red"))
            elif result.context_truncated:
                console.print("[dim](context compacted)[/dim]")
            else:
                console.print(result.response, end="")

            for tr in result.tool_results:
                style = "green" if tr.success else "red"
                title = "Tool Result" if tr.success else "Tool Error"
                panel_content = tr.content if tr.content else (tr.error or "")
                console.print(Panel(panel_content, title=title, border_style=style))

            if result.metrics:
                m = result.metrics
                # Ensure metrics start on a new line (response may have end="")
                console.print()
                console.print(
                    f"[dim]{m.total_tokens} tokens | {m.elapsed_seconds:.1f}s | {m.tokens_per_second:.1f} tok/s[/dim]"
                )
        console.print()


async def single_query_mode(query_loop: QueryLoop, query: str) -> None:
    query_loop.add_user_message(query)
    async for result in query_loop.run():
        if result.error:
            console.print(Panel(str(result.error), title="Error", border_style="red"))
        elif not result.context_truncated:
            console.print(result.response, end="")
        for tr in result.tool_results:
            style = "green" if tr.success else "red"
            title = "Tool Result" if tr.success else "Tool Error"
            panel_content = tr.content if tr.content else (tr.error or "")
            console.print(Panel(panel_content, title=title, border_style=style))

        if result.metrics:
            m = result.metrics
            # Ensure metrics start on a new line (response may have end="")
            console.print()
            console.print(
                f"[dim]{m.total_tokens} tokens | {m.elapsed_seconds:.1f}s | {m.tokens_per_second:.1f} tok/s[/dim]"
            )
    console.print()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(
    ctx: typer.Context,
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Print request URL and redacted headers to stderr"),
):
    """Run Vibe Agent in interactive or single-query mode."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Initialize Session Logger
    session_id = str(uuid.uuid4())[:8]
    logger = setup_session_logger(DEFAULT_CONFIG.logging, session_id)
    if DEFAULT_CONFIG.logging.enabled:
        logger.info(f"Starting session {session_id} in {working_dir}")

    # Use semantic model names for the fallback chain so the registry can resolve them
    fallback_chain = DEFAULT_CONFIG.get_fallback_chain()

    query_loop = QueryLoopFactory(
        base_url=server,
        model=model,
        api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
        working_dir=working_dir,
        fallback_chain=fallback_chain,
        config=DEFAULT_CONFIG,
        logger=logger,
        debug=debug,
    ).create()

    if ctx.args:
        query = " ".join(ctx.args)
        asyncio.run(single_query_mode(query_loop, query))
    else:
        asyncio.run(interactive_mode(query_loop))


@eval_app.command("run")
def run_evals(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter evals by tag"),
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Limit number of evals to run"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Print request URL and redacted headers to stderr"),
):
    """Run built-in eval cases and display results."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Initialize Session Logger
    session_id = str(uuid.uuid4())[:8]
    logger = setup_session_logger(DEFAULT_CONFIG.logging, session_id)
    if DEFAULT_CONFIG.logging.enabled:
        logger.info(f"Starting session {session_id} in {working_dir}")

    # Use semantic model names for the fallback chain so the registry can resolve them
    fallback_chain = DEFAULT_CONFIG.get_fallback_chain()

    query_loop = QueryLoopFactory(
        base_url=server,
        model=model,
        api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
        working_dir=working_dir,
        fallback_chain=fallback_chain,
        config=DEFAULT_CONFIG,
        logger=logger,
        debug=debug,
    ).create()

    store = EvalStore()
    cases = store.load_builtin_evals()
    if tag:
        cases = [c for c in cases if tag in c.tags]
    if limit is not None:
        cases = cases[:limit]

    if not cases:
        console.print("[yellow]No eval cases match the given filters.[/yellow]")
        raise typer.Exit(code=0)

    console.print(f"Running {len(cases)} eval(s)...\n")
    runner = EvalRunner(query_loop=query_loop, eval_store=store)
    results = asyncio.run(runner.run_all(cases))

    table = Table(title="Eval Results")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Diff", style="dim")

    passed_count = 0
    for case, result in zip(cases, results):
        status = "[green]✓ PASS[/green]" if result.passed else "[red]✗ FAIL[/red]"
        diff_text = "\n".join(f"{k}: {v}" for k, v in result.diff.items()) if result.diff else ""
        table.add_row(case.id, status, diff_text)
        if result.passed:
            passed_count += 1

    console.print(table)
    score = passed_count / len(results) if results else 0.0
    console.print(f"\nScore: {passed_count}/{len(results)} ({score:.0%})")

    if score < 1.0:
        raise typer.Exit(code=1)


@eval_app.command("update-baseline")
def update_baseline():
    """Update docs/baseline_scorecard.json from the latest eval run in EvalStore."""
    import json
    from collections import defaultdict

    store = EvalStore()
    summary = store.summary()
    results = store.get_results()

    by_subsystem = defaultdict(lambda: {"total": 0, "passed": 0})
    by_difficulty = defaultdict(lambda: {"total": 0, "passed": 0})

    cases = store.load_builtin_evals()
    case_map = {c.id: c for c in cases}

    for r in results:
        case = case_map.get(r["eval_id"])
        if not case:
            continue
        # Extract subsystem and difficulty from tags
        subsystem = "unknown"
        difficulty = "unknown"
        for tag in case.tags:
            if tag.startswith("subsystem="):
                subsystem = tag.split("=", 1)[1]
            elif tag.startswith("difficulty="):
                difficulty = tag.split("=", 1)[1]
        by_subsystem[subsystem]["total"] += 1
        by_difficulty[difficulty]["total"] += 1
        if r["passed"]:
            by_subsystem[subsystem]["passed"] += 1
            by_difficulty[difficulty]["passed"] += 1

    baseline = {
        "date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "overall_score": summary["score"],
        "total_cases": summary["total_runs"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "by_subsystem": {k: v for k, v in by_subsystem.items()},
        "by_difficulty": {k: v for k, v in by_difficulty.items()},
    }

    # Resolve baseline path relative to project root (where .git lives)
    project_root = Path(__file__).resolve().parents[2]
    baseline_path = project_root / "docs" / "baseline_scorecard.json"
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)

    console.print(f"[green]Baseline updated:[/green] {baseline_path}")
    console.print(f"Score: {summary['passed']}/{summary['total_runs']} ({summary['score']:.0%})")


@eval_app.command("soak")
def run_soak(
    duration: int = typer.Option(60, "--duration", "-d", help="Duration in minutes"),
    cpm: float = typer.Option(6.0, "--cpm", help="Cases per minute target"),
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    debug: bool = typer.Option(False, "--debug", help="Print request URL and redacted headers to stderr"),
):
    """Run a long-running soak test against built-in eval cases."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    from vibe.evals.soak_test import SoakTestRunner, print_report

    registry = ModelRegistry()
    fallback_chain = []
    for name in DEFAULT_CONFIG.get_fallback_chain():
        profile = registry.get(name)
        model_id = profile.model_id if profile else name
        fallback_chain.append(model_id)

    def factory():
        return QueryLoopFactory(
            base_url=server,
            model=model,
            api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
            working_dir=working_dir,
            fallback_chain=fallback_chain,
            debug=debug,
        ).create()

    store = EvalStore()
    cases = store.load_builtin_evals()
    if not cases:
        console.print("[yellow]No builtin eval cases found.[/yellow]")
        raise typer.Exit(code=1)

    runner = SoakTestRunner(
        query_loop_factory=factory,
        eval_store=store,
        model=model,
        base_url=server,
        duration_minutes=float(duration),
        cases_per_minute=cpm,
    )
    report = asyncio.run(runner.run(cases))
    print_report(report)


@memory_app.command("traces")
def list_traces(
    limit: int = typer.Option(20, "--limit", "-n", help="Max sessions to show"),
):
    """List recent trace sessions."""
    store = TraceStore()
    sessions = store.get_recent_sessions(limit=limit)
    if not sessions:
        console.print("[dim]No traces found.[/dim]")
        return
    table = Table(title="Recent Trace Sessions")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Start", style="dim")
    table.add_column("Model", style="magenta")
    table.add_column("Success", style="bold")
    for s in sessions:
        success = "[green]✓[/green]" if s.get("success") else "[red]✗[/red]"
        table.add_row(s.get("id", "?"), s.get("start_time", "?"), s.get("model", "?"), success)
    console.print(table)


if __name__ == "__main__":
    app()

"""Main CLI entry point for Vibe Agent."""

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vibe.core.config import VibeConfig
from vibe.core.query_loop import QueryLoop
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.evals.model_registry import ModelRegistry
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalStore

app = typer.Typer(help="Vibe Agent — an open agent harness platform")
eval_app = typer.Typer(help="Run and manage evals")
app.add_typer(eval_app, name="eval")
console = Console()

DEFAULT_CONFIG = VibeConfig.load()


async def interactive_mode(query_loop: QueryLoop) -> None:
    console.print("[bold green]Vibe Agent[/bold green] ready. Type /exit to quit, /clear to reset.")
    while True:
        try:
            user_input = console.input("[bold cyan]>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("/exit", "exit", "quit"):
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
    console.print()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(
    ctx: typer.Context,
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
):
    """Run Vibe Agent in interactive or single-query mode."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    registry = ModelRegistry()
    fallback_chain = []
    for name in DEFAULT_CONFIG.get_fallback_chain():
        profile = registry.get(name)
        model_id = profile.model_id if profile else name
        fallback_chain.append(model_id)

    query_loop = QueryLoopFactory(
        base_url=server,
        model=model,
        api_key=api_key,
        working_dir=working_dir,
        fallback_chain=fallback_chain,
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
):
    """Run built-in eval cases and display results."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    registry = ModelRegistry()
    fallback_chain = []
    for name in DEFAULT_CONFIG.get_fallback_chain():
        profile = registry.get(name)
        model_id = profile.model_id if profile else name
        fallback_chain.append(model_id)

    query_loop = QueryLoopFactory(
        base_url=server,
        model=model,
        api_key=api_key,
        working_dir=working_dir,
        fallback_chain=fallback_chain,
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


if __name__ == "__main__":
    app()

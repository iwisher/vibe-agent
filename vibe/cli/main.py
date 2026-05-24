"""Main CLI entry point for Vibe Agent."""

import asyncio
import readline
import uuid
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vibe.cli.skill_commands import app as skill_app
from vibe.core.config import VibeConfig
from vibe.core.logger import setup_session_logger
from vibe.core.query_loop import QueryLoop
from vibe.core.query_loop_factory import QueryLoopFactory
from vibe.evals.model_registry import ModelRegistry
from vibe.evals.runner import EvalRunner
from vibe.harness.memory.eval_store import EvalStore
from vibe.harness.memory.trace_store import TraceStore

app = typer.Typer(help="Vibe Agent — an open agent harness platform")
eval_app = typer.Typer(help="Run and manage evals")
app.add_typer(eval_app, name="eval")
memory_app = typer.Typer(help="Inspect stored traces and eval results")
app.add_typer(memory_app, name="memory")
app.add_typer(skill_app, name="skill")

# Phase 3.2: Session management commands
session_app = typer.Typer(help="Session management — list and resume incomplete sessions")
app.add_typer(session_app, name="session")

# Phase A: Preference layer commands
pref_app = typer.Typer(help="Preference management")
app.add_typer(pref_app, name="pref")

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
    verbose_mode = False
    show_reasoning = query_loop.config.llm.show_reasoning if (query_loop.config and hasattr(query_loop.config, "llm")) else True
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
        if user_input.lower() == "/verbose":
            verbose_mode = not verbose_mode
            status = "enabled" if verbose_mode else "disabled"
            console.print(f"Verbose mode {status}.")
            continue
        if user_input.lower() == "/reasoning":
            show_reasoning = not show_reasoning
            if query_loop.config and hasattr(query_loop.config, "llm"):
                query_loop.config.llm.show_reasoning = show_reasoning
            status = "enabled" if show_reasoning else "disabled"
            console.print(f"Reasoning display {status}.")
            continue
        if user_input.lower() == "/resume":
            from vibe.core.query_loop import QueryLoop
            from vibe.harness.memory.session_store import SessionStore

            store = SessionStore()
            incomplete = store.list_incomplete(limit=1)
            if not incomplete:
                console.print("[yellow]No incomplete sessions found.[/yellow]")
                continue
            session_id = incomplete[0]["session_id"]
            # Build a factory matching the current loop's config
            factory = QueryLoopFactory(
                base_url=DEFAULT_CONFIG.llm.base_url,
                model=query_loop.llm.model,
                api_key=DEFAULT_CONFIG.resolve_api_key(),
                working_dir=str(Path.cwd()),
                fallback_chain=DEFAULT_CONFIG.get_fallback_chain(),
                config=DEFAULT_CONFIG,
                logger=query_loop.logger,
            )
            try:
                query_loop = await QueryLoop.resume(session_id, store, factory)
                console.print(
                    f"[green]Resumed session {session_id[:16]}...[/green] "
                    f"(state: {query_loop.state.name}, iteration: {query_loop._iteration})"
                )
            except ValueError as e:
                console.print(f"[red]Failed to resume: {e}[/red]")
            continue

        query_loop.add_user_message(user_input)
        streamed_any = False
        status_spinner = None
        try:
            async for result in query_loop.run():
                if result.is_status:
                    if verbose_mode:
                        console.print(f"[dim]  → {result.status_message}[/dim]")
                    else:
                        if status_spinner is None:
                            status_spinner = console.status("[dim]Thinking...[/dim]", spinner="dots")
                            status_spinner.start()
                        status_spinner.update(f"[dim]{result.status_message}[/dim]")
                    continue

                # Exit spinner before printing stream chunks
                if status_spinner is not None:
                    status_spinner.stop()
                    status_spinner = None

                if result.is_stream_chunk:
                    streamed_any = True
                    if result.response:
                        console.print(result.response, end="")
                    if (verbose_mode or show_reasoning) and result.reasoning_content:
                        console.print(f"[dim]{result.reasoning_content}[/dim]", end="")
                    continue

                if result.error:
                    error_msg = str(result.error)
                    if getattr(result, "actionable_hint", None):
                        error_msg += f"\n\n[bold]Hint:[/bold] {result.actionable_hint}"
                    if getattr(result, "model_used", None):
                        error_msg += f"\n\n[bold]Model Used:[/bold] {result.model_used}"
                    console.print(Panel(error_msg, title="Error", border_style="red"))
                elif result.context_truncated:
                    console.print("[dim](context compacted)[/dim]")
                else:
                    if not streamed_any:
                        if (verbose_mode or show_reasoning) and result.reasoning_content:
                            console.print(f"[dim]{result.reasoning_content}[/dim]", end="")
                        console.print(result.response, end="")
                    else:
                        console.print()

                for tr in result.tool_results:
                    style = "green" if tr.success else "red"
                    title = "Tool Result" if tr.success else "Tool Error"
                    panel_content = tr.content if tr.content else (tr.error or "")
                    console.print(Panel(panel_content, title=title, border_style=style))

                if result.metrics:
                    m = result.metrics
                    # Ensure metrics start on a new line
                    if not streamed_any:
                        console.print()
                    
                    reasoning_part = ""
                    if getattr(m, "reasoning_tokens", 0) > 0:
                        reasoning_part = f" ({m.reasoning_tokens} reasoning)"

                    metrics_str = (
                        f"{m.total_tokens} tokens{reasoning_part} | "
                        f"{m.elapsed_seconds:.1f}s | "
                        f"{m.tokens_per_second:.1f} tok/s"
                    )
                    if getattr(result, "model_used", None) and result.model_used != query_loop.llm.model:
                        metrics_str += f" (via fallback model: {result.model_used})"
                    console.print(f"[dim]{metrics_str}[/dim]")
        finally:
            if status_spinner is not None:
                status_spinner.stop()


async def single_query_mode(query_loop: QueryLoop, query: str) -> None:
    query_loop.add_user_message(query)
    streamed_any = False
    show_reasoning = query_loop.config.llm.show_reasoning if (query_loop.config and hasattr(query_loop.config, "llm")) else True
    status_spinner = None
    try:
        async for result in query_loop.run():
            if result.is_status:
                if status_spinner is None:
                    status_spinner = console.status("[dim]Thinking...[/dim]", spinner="dots")
                    status_spinner.start()
                status_spinner.update(f"[dim]{result.status_message}[/dim]")
                continue

            # Exit spinner before printing stream chunks
            if status_spinner is not None:
                status_spinner.stop()
                status_spinner = None

            if result.is_stream_chunk:
                streamed_any = True
                if result.response:
                    console.print(result.response, end="")
                if show_reasoning and result.reasoning_content:
                    console.print(f"[dim]{result.reasoning_content}[/dim]", end="")
                continue

            if result.error:
                error_msg = str(result.error)
                if getattr(result, "actionable_hint", None):
                    error_msg += f"\n\n[bold]Hint:[/bold] {result.actionable_hint}"
                if getattr(result, "model_used", None):
                    error_msg += f"\n\n[bold]Model Used:[/bold] {result.model_used}"
                console.print(Panel(error_msg, title="Error", border_style="red"))
            elif result.context_truncated:
                console.print("[dim](context compacted)[/dim]")
            else:
                if not streamed_any:
                    if show_reasoning and result.reasoning_content:
                        console.print(f"[dim]{result.reasoning_content}[/dim]", end="")
                    console.print(result.response, end="")
                else:
                    console.print()

            for tr in result.tool_results:
                style = "green" if tr.success else "red"
                title = "Tool Result" if tr.success else "Tool Error"
                panel_content = tr.content if tr.content else (tr.error or "")
                console.print(Panel(panel_content, title=title, border_style=style))

            if result.metrics:
                m = result.metrics
                # Ensure metrics start on a new line
                if not streamed_any:
                    console.print()
                
                reasoning_part = ""
                if getattr(m, "reasoning_tokens", 0) > 0:
                    reasoning_part = f" ({m.reasoning_tokens} reasoning)"

                metrics_str = (
                    f"{m.total_tokens} tokens{reasoning_part} | "
                    f"{m.elapsed_seconds:.1f}s | "
                    f"{m.tokens_per_second:.1f} tok/s"
                )
                if getattr(result, "model_used", None) and result.model_used != query_loop.llm.model:
                    metrics_str += f" (via fallback model: {result.model_used})"
                console.print(f"[dim]{metrics_str}[/dim]")
    finally:
        if status_spinner is not None:
            status_spinner.stop()
    console.print()


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def main(
    ctx: typer.Context,
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Print request URL and redacted headers to stderr"
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Enable/disable streaming responses"),
):
    """Run Vibe Agent in interactive or single-query mode."""
    working_dir = str(Path(working_dir).expanduser().resolve())

    # Use semantic model names for the fallback chain so the registry can resolve them
    fallback_chain = DEFAULT_CONFIG.get_fallback_chain()

    # Phase 3.2: Check for incomplete sessions before creating a fresh QueryLoop
    session_cfg = getattr(DEFAULT_CONFIG, "session", None)
    should_resume = False
    resumed_session_id: str | None = None

    if not ctx.args and session_cfg is not None:
        from vibe.harness.memory.session_store import SessionStore

        store = SessionStore()
        # Auto-cleanup stale checkpoints on startup (older than 24h, non-terminal)
        try:
            removed = store.cleanup_stale(max_age_hours=24.0)
            if removed:
                console.print(f"[dim]Cleaned up {removed} stale session checkpoint(s).[/dim]")
        except Exception:
            pass  # Non-fatal: don't block startup if cleanup fails

        incomplete = store.list_incomplete(limit=1)
        if incomplete:
            latest = incomplete[0]
            latest_id = latest["session_id"]
            if getattr(session_cfg, "auto_resume", False):
                should_resume = True
                resumed_session_id = latest_id
                console.print(
                    f"[dim]Auto-resuming session {latest_id[:16]}...[/dim]"
                )
            elif getattr(session_cfg, "prompt_on_resume", True):
                console.print(
                    f"[yellow]You have an incomplete session ({latest_id[:16]}...).[/yellow]"
                )
                choice = input("Resume latest session? [y/n]: ").strip().lower()
                if choice == "y":
                    should_resume = True
                    resumed_session_id = latest_id

    # Initialize Session Logger
    if should_resume and resumed_session_id:
        session_id = resumed_session_id[:8]
    else:
        session_id = str(uuid.uuid4())[:8]
    logger = setup_session_logger(DEFAULT_CONFIG.logging, session_id)
    if DEFAULT_CONFIG.logging.enabled:
        logger.info(f"Starting session {session_id} in {working_dir}")

    if should_resume and resumed_session_id:
        # Resume existing session
        from vibe.core.query_loop import QueryLoop
        from vibe.harness.memory.session_store import SessionStore

        factory = QueryLoopFactory(
            base_url=server,
            model=model,
            api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
            working_dir=working_dir,
            fallback_chain=fallback_chain,
            config=DEFAULT_CONFIG,
            logger=logger,
            debug=debug,
            stream=stream,
        )

        async def _run_resumed():
            store = SessionStore()
            loop = await QueryLoop.resume(resumed_session_id, store, factory)
            console.print(
                f"[green]Resumed session {resumed_session_id[:16]}...[/green] "
                f"(state: {loop.state.name}, iteration: {loop._iteration})"
            )
            await interactive_mode(loop)

        asyncio.run(_run_resumed())
    else:
        query_loop = QueryLoopFactory(
            base_url=server,
            model=model,
            api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
            working_dir=working_dir,
            fallback_chain=fallback_chain,
            config=DEFAULT_CONFIG,
            logger=logger,
            debug=debug,
            stream=stream,
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
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Print request URL and redacted headers to stderr"
    ),
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
        "date": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
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
    debug: bool = typer.Option(
        False, "--debug", help="Print request URL and redacted headers to stderr"
    ),
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


# --- Wiki sub-commands ---

wiki_app = typer.Typer(help="Manage the LLM Wiki knowledge base")
memory_app.add_typer(wiki_app, name="wiki")
wiki_index_app = typer.Typer(help="Wiki index management")
wiki_app.add_typer(wiki_index_app, name="index")


def _get_wiki() -> "Any":
    """Get a configured LLMWiki instance."""
    from vibe.memory.wiki import LLMWiki

    return LLMWiki(base_path="~/.vibe/wiki")


@wiki_app.command("list")
def wiki_list(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Filter by status (draft|verified)"
    ),
):
    """List wiki pages."""
    import asyncio

    wiki = _get_wiki()
    pages = asyncio.run(wiki.list_pages(tag=tag, status=status))
    if not pages:
        console.print("[dim]No wiki pages found.[/dim]")
        return
    table = Table(title="Wiki Pages")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Status", style="dim")
    table.add_column("Tags")
    table.add_column("Updated", style="dim")
    for p in pages:
        status_style = (
            "[green]verified[/green]" if p.status == "verified" else "[yellow]draft[/yellow]"
        )
        table.add_row(p.id[:8], p.title, status_style, ", ".join(p.tags), p.last_updated)
    console.print(table)


@wiki_app.command("search")
def wiki_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Search wiki pages (BM25)."""
    import asyncio

    wiki = _get_wiki()
    pages = asyncio.run(wiki.search_pages(query=query, limit=limit))
    if not pages:
        console.print(f"[dim]No results for '{query}'.[/dim]")
        return
    for p in pages:
        console.print(f"[bold cyan]{p.title}[/bold cyan] [dim]({p.id[:8]})[/dim]")
        console.print(f"  Tags: {', '.join(p.tags)}  |  Status: {p.status}")
        snippet = p.content[:200].replace("\n", " ")
        console.print(f"  {snippet}...")
        console.print()


@wiki_app.command("show")
def wiki_show(
    page_id: str = typer.Argument(..., help="Page ID (or slug)"),
):
    """Show a wiki page with rendered links."""
    import asyncio

    wiki = _get_wiki()
    # Try by ID, then by slug
    page = asyncio.run(wiki.get_page(page_id))
    if page is None:
        page = asyncio.run(wiki.get_page_by_slug(page_id))
    if page is None:
        console.print(f"[red]Page not found: {page_id}[/red]")
        raise typer.Exit(code=1)
    console.print(
        Panel(
            f"[bold]{page.title}[/bold]\n"
            f"ID: {page.id}\nStatus: {page.status}\nTags: {', '.join(page.tags)}\n"
            f"Created: {page.date_created} | Updated: {page.last_updated}\n"
            f"Citations: {len(page.citations)}\n\n{page.content}",
            title=f"Wiki: {page.title}",
            border_style="cyan",
        )
    )


@wiki_app.command("create")
def wiki_create(
    title: str = typer.Option(..., "--title", "-t", help="Page title"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    content: str = typer.Option(
        "", "--content", "-c", help="Initial content (or opens $EDITOR if empty)"
    ),
):
    """Create a new wiki page. Opens $EDITOR if no --content provided."""
    import asyncio
    import os
    import subprocess
    import tempfile

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    if not content:
        # Open $EDITOR for content input
        editor = os.environ.get("EDITOR", "nano")
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(f"# {title}\n\n")
            tmp_path = f.name
        try:
            subprocess.run([editor, tmp_path], check=True)
            with open(tmp_path) as f:
                content = f.read().strip()
        finally:
            os.unlink(tmp_path)

    if not content:
        console.print("[yellow]No content provided. Aborting.[/yellow]")
        raise typer.Exit(code=1)

    wiki = _get_wiki()
    page = asyncio.run(wiki.create_page(title=title, content=content, tags=tag_list))
    console.print(
        f"[green]✓[/green] Created wiki page: [bold]{page.title}[/bold] (ID: {page.id[:8]})"
    )


@wiki_app.command("edit")
def wiki_edit(
    page_id: str = typer.Argument(..., help="Page ID or slug"),
):
    """Edit a wiki page in $EDITOR."""
    import asyncio
    import os
    import subprocess
    import tempfile

    wiki = _get_wiki()
    page = asyncio.run(wiki.get_page(page_id))
    if page is None:
        page = asyncio.run(wiki.get_page_by_slug(page_id))
    if page is None:
        console.print(f"[red]Page not found: {page_id}[/red]")
        raise typer.Exit(code=1)

    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(page.content)
        tmp_path = f.name

    try:
        subprocess.run([editor, tmp_path], check=True)
        with open(tmp_path) as f:
            new_content = f.read().strip()
    finally:
        os.unlink(tmp_path)

    if new_content == page.content:
        console.print("[dim]No changes made.[/dim]")
        return

    updated = asyncio.run(wiki.update_page(page.id, content=new_content))
    console.print(f"[green]✓[/green] Updated: [bold]{updated.title}[/bold]")


@wiki_index_app.command("rebuild")
def wiki_index_rebuild():
    """Rebuild the wiki page index (full rebuild)."""
    from vibe.memory.pageindex import PageIndex

    wiki = _get_wiki()
    pageindex = PageIndex(index_path="~/.vibe/memory/index.json")
    console.print("Rebuilding wiki index...")
    pageindex.rebuild(wiki, incremental=False)
    console.print("[green]✓[/green] Wiki index rebuilt.")


@wiki_app.command("expire")
def wiki_expire(
    days: int = typer.Option(30, "--days", "-d", help="Expire draft wiki pages older than N days"),
):
    """Expire draft wiki pages older than N days."""
    import asyncio

    wiki = _get_wiki()
    count = asyncio.run(wiki.expire_drafts(cutoff_days=days))
    if count == 0:
        console.print(f"[dim]No draft pages older than {days} days found.[/dim]")
    else:
        console.print(
            f"[green]✓[/green] Expired {count} draft wiki page(s) older than {days} days."
        )


@wiki_app.command("compile")
def wiki_compile(
    hours: int = typer.Option(24, "--hours", "-h", help="Look back N hours for sessions"),
    novelty: float = typer.Option(0.5, "--novelty", "-n", help="Novelty threshold (0.0-1.0)"),
    confidence: float = typer.Option(
        0.8, "--confidence", "-c", help="Confidence threshold (0.0-1.0)"
    ),
):
    """Compile recent trace sessions into pending wiki pages for review."""
    import asyncio

    from vibe.core.query_loop_factory import QueryLoopFactory
    from vibe.harness.memory.trace_store import TraceStore
    from vibe.memory.compiler import WikiCompiler

    wiki = _get_wiki()
    trace_store = TraceStore()
    # Reuse the factory to get an LLM client for extraction
    factory = QueryLoopFactory(
        base_url=DEFAULT_CONFIG.llm.base_url,
        model=DEFAULT_CONFIG.llm.default_model,
        api_key=DEFAULT_CONFIG.resolve_api_key(),
        config=DEFAULT_CONFIG,
    )
    llm_client = factory.create_llm()

    compiler = WikiCompiler(
        trace_store=trace_store,
        wiki=wiki,
        llm_client=llm_client,
        config=DEFAULT_CONFIG,
    )
    summary = asyncio.run(
        compiler.compile_recent(
            hours=hours,
            novelty_threshold=novelty,
            confidence_threshold=confidence,
        )
    )
    console.print("[green]✓[/green] Compilation complete:")
    console.print(f"  Sessions scanned: {summary.sessions_scanned}")
    console.print(f"  Items extracted: {summary.items_extracted}")
    console.print(f"  Items approved: {summary.items_approved}")
    console.print(f"  Pages created: {summary.pages_created}")
    if summary.errors:
        console.print(f"  [yellow]Errors: {summary.errors}[/yellow]")


@wiki_app.command("review")
def wiki_review(
    auto_approve: bool = typer.Option(
        False, "--auto-approve", "-a", help="Approve all pending pages"
    ),
    list_only: bool = typer.Option(False, "--list", "-l", help="List pending pages without action"),
):
    """Review pending wiki pages. Approve, reject, or list them."""
    import asyncio

    from vibe.memory.compiler import WikiCompiler

    wiki = _get_wiki()
    compiler = WikiCompiler(
        trace_store=None,  # Not needed for review
        wiki=wiki,
        llm_client=None,  # Not needed for review
    )

    pending = asyncio.run(compiler.list_pending())
    if not pending:
        console.print("[dim]No pending pages awaiting review.[/dim]")
        raise typer.Exit(code=0)

    table = Table(title="Pending Wiki Pages")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="bold")
    table.add_column("Tags")
    table.add_column("Created", style="dim")
    for p in pending:
        table.add_row(p.id[:8], p.title, ", ".join(p.tags), p.date_created)
    console.print(table)

    if list_only:
        return

    if auto_approve:
        result = asyncio.run(compiler.review_all(auto_approve=True))
        console.print(f"[green]✓[/green] Auto-approved {result['approved']} page(s).")
        return

    # Interactive review
    for p in pending:
        console.print(f"\n[bold]{p.title}[/bold] [dim]({p.id[:8]})[/dim]")
        snippet = p.content[:300].replace("\n", " ")
        console.print(f"  {snippet}...")
        choice = typer.prompt("Approve? [y/n/s] (y=yes, n=no, s=skip)", default="s")
        if choice.lower() == "y":
            try:
                asyncio.run(compiler.approve_page(p.id))
                console.print("  [green]Approved[/green]")
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
        elif choice.lower() == "n":
            try:
                asyncio.run(compiler.reject_page(p.id))
                console.print("  [red]Rejected[/red]")
            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
        else:
            console.print("  [dim]Skipped[/dim]")


@memory_app.command("status")
def memory_status():
    """Show tripartite memory system status: wiki pages, index size, telemetry summary."""
    import asyncio
    import json
    from pathlib import Path

    wiki = _get_wiki()
    base_path = Path(wiki.base_path)

    # Count pages
    counts = asyncio.run(wiki.get_status_counts())
    total_pages = counts["total"]
    verified_pages = counts["verified"]
    draft_pages = counts["draft"]

    # Index size
    index_path = base_path / ".slug_index.json"
    index_entries = 0
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text())
            index_entries = len(data.get("slug_to_id", {}))
        except (json.JSONDecodeError, OSError):
            pass

    # Telemetry summary (last 24h)
    sessions_24h = 0
    avg_duration = 0.0
    compactions_24h = 0
    if wiki.db is not None:
        try:
            import time

            cutoff = time.time() - 86400
            cursor = wiki.db.conn.execute(
                "SELECT COUNT(*), AVG(duration_seconds) FROM _telemetry WHERE type = 'session' AND timestamp > ?",
                (cutoff,),
            )
            row = cursor.fetchone()
            if row:
                sessions_24h = row[0] or 0
                avg_duration = row[1] or 0.0

            cursor = wiki.db.conn.execute(
                "SELECT COUNT(*) FROM _telemetry WHERE type = 'compaction' AND timestamp > ?",
                (cutoff,),
            )
            compactions_24h = cursor.fetchone()[0] or 0
        except Exception as e:
            import logging

            logging.getLogger("vibe.cli").debug(
                "Failed to fetch telemetry for memory status: %s", e
            )

    # Print status
    table = Table(title="Tripartite Memory Status")
    table.add_column("Component", style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", style="dim")

    table.add_row("Wiki", "Total pages", str(total_pages))
    table.add_row("Wiki", "Verified", f"[green]{verified_pages}[/green]")
    table.add_row("Wiki", "Draft", f"[yellow]{draft_pages}[/yellow]")
    table.add_row("Index", "Entries", str(index_entries))
    table.add_row("Telemetry (24h)", "Sessions", str(sessions_24h))
    table.add_row("Telemetry (24h)", "Avg duration", f"{avg_duration:.1f}s")
    table.add_row("Telemetry (24h)", "Compactions", str(compactions_24h))

    console.print(table)


@memory_app.command("import")
def import_cmd(
    path: str = typer.Argument(..., help="Path to the file or directory to ingest"),
):
    """Import documents (PDF, MD, DOCX, etc.) into the Tripartite Memory System.

    Uses IBM Docling under the hood to semantically extract and format documents.
    """
    import asyncio

    from rich.progress import Progress, SpinnerColumn, TextColumn

    from vibe.cli.main import DEFAULT_CONFIG
    from vibe.core.query_loop_factory import QueryLoopFactory

    # Initialize the wiki and extractor via factory
    factory = QueryLoopFactory(
        base_url=DEFAULT_CONFIG.llm.base_url,
        model=DEFAULT_CONFIG.llm.default_model,
        api_key=DEFAULT_CONFIG.resolve_api_key(),
        config=DEFAULT_CONFIG,
    )
    wiki, pageindex, telemetry = factory._create_tripartite(DEFAULT_CONFIG.memory)
    if not wiki:
        console.print("[red]Memory system is not enabled or failed to initialize.[/red]")
        raise typer.Exit(1)

    try:
        from vibe.memory.extraction import KnowledgeExtractor
        from vibe.memory.ingestion.worker import IngestionWorker
    except ImportError as e:
        console.print(f"[red]Missing dependencies: {e}[/red]")
        console.print(
            "Make sure you install the ingest extras: pip install vibe-agent[ingest] or docling"
        )
        raise typer.Exit(1)

    extractor = KnowledgeExtractor(
        wiki=wiki,
        pageindex=pageindex,
        telemetry=telemetry,
        llm_client=factory.create_llm(),
    )
    worker = IngestionWorker(extractor=extractor)

    async def run_import():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
        ) as progress:
            progress.add_task(
                description=f"Parsing and ingesting {path} via Docling...", total=None
            )
            try:
                pages_created = await worker.ingest_file(path)
                console.print(
                    f"[green]Successfully ingested {path}. Created {pages_created} Wiki Pages.[/green]"
                )
            except Exception as e:
                console.print(f"[red]Import failed: {e}[/red]")

    asyncio.run(run_import())


# --- Session sub-commands (Phase 3.2) ---


@session_app.command("list")
def session_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max sessions to show"),
):
    """List incomplete sessions that can be resumed."""
    from vibe.harness.memory.session_store import SessionStore

    store = SessionStore()
    sessions = store.list_incomplete(limit=limit)
    if not sessions:
        console.print("[dim]No incomplete sessions found.[/dim]")
        return

    table = Table(title="Incomplete Sessions")
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("State", style="bold")
    table.add_column("Iteration", style="dim")
    table.add_column("Model", style="magenta")
    table.add_column("Updated", style="dim")

    for s in sessions:
        table.add_row(
            s.get("session_id", "?")[:16],
            s.get("state", "?"),
            str(s.get("iteration", 0)),
            s.get("model", "?") or "?",
            s.get("updated_at", "?"),
        )
    console.print(table)


@session_app.command("cleanup")
def session_cleanup(
    stale_only: bool = typer.Option(
        True, "--stale-only/--all", help="Only remove non-terminal stale checkpoints"
    ),
    max_age_hours: float = typer.Option(
        24.0, "--max-age", "-h", help="Maximum age in hours for stale checkpoints"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be deleted without deleting"
    ),
):
    """Remove stale session checkpoints that weren't cleaned up on exit."""
    from vibe.harness.memory.session_store import SessionStore

    store = SessionStore()
    stats = store.get_checkpoint_stats()

    console.print(f"[dim]Total checkpoints: {stats['total']}[/dim]")
    if stats["by_state"]:
        for state, count in sorted(stats["by_state"].items()):
            console.print(f"[dim]  {state}: {count}[/dim]")

    if dry_run:
        # Count what would be deleted without actually deleting
        if stale_only:
            # We need to count stale checkpoints manually
            import sqlite3
            from contextlib import closing
            from datetime import datetime, timedelta, timezone

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
            with closing(sqlite3.connect(store.db_path, timeout=5.0)) as conn:
                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM session_checkpoints
                    WHERE updated_at < ?
                      AND state NOT IN ('COMPLETED', 'ERROR', 'STOPPED', 'INCOMPLETE')
                    """,
                    (cutoff,),
                )
                would_delete = cursor.fetchone()[0]
        else:
            import sqlite3
            from contextlib import closing
            from datetime import datetime, timedelta, timezone

            cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()
            with closing(sqlite3.connect(store.db_path, timeout=5.0)) as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM session_checkpoints WHERE updated_at < ?",
                    (cutoff,),
                )
                would_delete = cursor.fetchone()[0]
        console.print(f"[yellow]Dry run: would delete {would_delete} checkpoint(s)[/yellow]")
        return

    if stale_only:
        removed = store.cleanup_stale(max_age_hours=max_age_hours)
    else:
        removed = store.cleanup_all(max_age_hours=max_age_hours)

    if removed:
        console.print(f"[green]Deleted {removed} checkpoint(s).[/green]")
    else:
        console.print("[dim]No stale checkpoints found.[/dim]")


@session_app.command("resume")
def session_resume(
    session_id: str | None = typer.Argument(
        None, help="Session ID to resume (default: latest incomplete)"
    ),
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Print request URL and redacted headers to stderr"
    ),
):
    """Resume an incomplete session from a checkpoint."""
    from vibe.core.query_loop import QueryLoop
    from vibe.harness.memory.session_store import SessionStore

    working_dir = str(Path(working_dir).expanduser().resolve())
    store = SessionStore()

    # Resolve session_id
    if session_id is None:
        sessions = store.list_incomplete(limit=1)
        if not sessions:
            console.print(
                "[yellow]No incomplete sessions found. Start a new session with `vibe`.[/yellow]"
            )
            raise typer.Exit(code=0)
        session_id = sessions[0]["session_id"]
        console.print(f"[dim]Resuming latest session: {session_id[:16]}...[/dim]\n")

    # Verify checkpoint exists
    if not store.has_checkpoint(session_id):
        console.print(f"[red]No checkpoint found for session {session_id[:16]}.[/red]")
        raise typer.Exit(code=1)

    # Initialize Session Logger
    logger = setup_session_logger(DEFAULT_CONFIG.logging, session_id[:8])
    if DEFAULT_CONFIG.logging.enabled:
        logger.info(f"Resuming session {session_id} in {working_dir}")

    # Create factory
    fallback_chain = DEFAULT_CONFIG.get_fallback_chain()
    factory = QueryLoopFactory(
        base_url=server,
        model=model,
        api_key=api_key if api_key is not None else DEFAULT_CONFIG.resolve_api_key(),
        working_dir=working_dir,
        fallback_chain=fallback_chain,
        config=DEFAULT_CONFIG,
        logger=logger,
        debug=debug,
    )

    async def _run_resume():
        loop = await QueryLoop.resume(session_id, store, factory)
        console.print(
            f"[green]✓[/green] Resumed session [bold]{session_id[:16]}[/bold] (state: {loop.state.name}, iteration: {loop._iteration})"
        )
        console.print(
            "[dim]Continue the conversation. Type /exit to quit, /clear to reset.[/dim]\n"
        )
        await interactive_mode(loop)

    try:
        asyncio.run(_run_resume())
    except ValueError as e:
        console.print(f"[red]Failed to resume: {e}[/red]")
        raise typer.Exit(code=1)


# --- Dashboard sub-commands (Phase 5.1) ---

dashboard_app = typer.Typer(help="Launch web dashboard for session observability")
app.add_typer(dashboard_app, name="dashboard")


@dashboard_app.command("start")
def dashboard_start(
    port: int = typer.Option(8080, "--port", "-p", help="Port to run dashboard on"),
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="Host to bind to"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    no_auth: bool = typer.Option(
        False, "--no-auth", help="Disable token authentication (dev only)"
    ),
):
    """Launch the Vibe Agent trace dashboard (FastAPI + React)."""
    import webbrowser

    from vibe.dashboard.server import run_server

    url, token = run_server(host=host, port=port, enable_auth=not no_auth)

    if token:
        console.print(f"[green]Starting dashboard at {url}[/green]")
        console.print(
            f"[dim]Dashboard token: {token[:16]}... (pass via ?token= or X-Dashboard-Token header)[/dim]"
        )
    else:
        console.print(f"[green]Starting dashboard at {url} (no auth)[/green]")

    if not no_browser:
        # Open browser after a short delay to let server start
        import threading

        def open_browser():
            import time

            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    try:
        import uvicorn

        from vibe.dashboard.server import app

        uvicorn.run(app, host=host, port=port, log_level="info")
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")


# --- Shadow workspace sub-commands (Phase 5.2) ---

shadow_app = typer.Typer(help="Shadow workspace rollback management")
app.add_typer(shadow_app, name="shadow")


@shadow_app.command("list")
def shadow_list():
    """List all shadow branches (workspace checkpoints)."""
    from vibe.tools.git_shadow import ShadowBranchManager

    manager = ShadowBranchManager()
    shadows = manager.list_shadows()

    if not shadows:
        console.print(
            "[dim]No shadow branches found. Run `vibe shadow create` before write-heavy tasks.[/dim]"
        )
        return

    table = Table(title="Shadow Branches")
    table.add_column("Session ID", style="cyan")
    table.add_column("Branch", style="dim")
    table.add_column("Original", style="magenta")
    table.add_column("Restorable", style="bold")

    for s in shadows:
        table.add_row(
            s.session_id[:16],
            s.branch_name,
            s.original_branch,
            "[green]yes[/green]" if s.restorable else "[red]no[/red]",
        )
    console.print(table)


@shadow_app.command("create")
def shadow_create(
    session_id: str = typer.Argument(..., help="Session ID to create shadow for"),
):
    """Create a shadow branch for the current workspace state."""
    from vibe.tools.git_shadow import ShadowBranchManager

    manager = ShadowBranchManager()
    shadow = manager.create_shadow(session_id)

    if shadow is None:
        console.print(
            "[yellow]Not in a git repository or git not available. Shadow not created.[/yellow]"
        )
        raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Created shadow branch [bold]{shadow.branch_name}[/bold]")
    console.print(f"  Original branch: {shadow.original_branch}")
    console.print(f"  Uncommitted changes: {'yes' if shadow.has_uncommitted_changes else 'no'}")


@shadow_app.command("restore")
def shadow_restore(
    session_id: str = typer.Argument(..., help="Session ID to restore from shadow"),
):
    """Restore workspace from a shadow branch."""
    from vibe.tools.git_shadow import ShadowBranchManager

    manager = ShadowBranchManager()
    success = manager.restore_shadow(session_id)

    if success:
        console.print(
            f"[green]✓[/green] Restored workspace from shadow for session {session_id[:16]}"
        )
        console.print(
            "[yellow]You are now on the shadow branch. Use `git checkout <branch>` to return to original.[/yellow]"
        )
    else:
        console.print(f"[red]Failed to restore shadow for session {session_id[:16]}.[/red]")
        raise typer.Exit(code=1)


@shadow_app.command("clean")
def shadow_clean(
    days: int = typer.Option(7, "--older-than", "-d", help="Remove shadows older than N days"),
):
    """Clean up old shadow branches."""
    from vibe.tools.git_shadow import ShadowBranchManager

    manager = ShadowBranchManager()
    removed = manager.clean_shadows(older_than_days=days)
    console.print(f"[green]Removed {removed} shadow branches older than {days} days.[/green]")


@shadow_app.command("rollback")
def shadow_rollback(
    session_id: str | None = typer.Argument(None, help="Session ID to rollback (default: latest)"),
):
    """Alias for `vibe shadow restore` — restore workspace from latest shadow."""
    if session_id is None:
        from vibe.tools.git_shadow import ShadowBranchManager

        manager = ShadowBranchManager()
        shadows = manager.list_shadows()
        if not shadows:
            console.print("[red]No shadows found. Cannot rollback.[/red]")
            raise typer.Exit(code=1)
        session_id = shadows[-1].session_id
        console.print(f"[dim]Rolling back latest shadow: {session_id[:16]}...[/dim]")

    # Delegate to restore
    shadow_restore(session_id)


# --- Preference sub-commands (Phase A) ---


@pref_app.command("tool-set")
def pref_tool_set(
    tool_name: str = typer.Argument(..., help="Tool name or glob pattern"),
    args: str = typer.Argument(..., help="JSON dict of default args"),
):
    """Set default arguments for a tool."""
    import json

    from vibe.preferences.tool_prefs import ToolPreferenceRegistry

    registry = ToolPreferenceRegistry()
    parsed = json.loads(args)
    rule = registry.set_default_args(tool_name, parsed)
    console.print(f"[green]✓[/green] Set defaults for [bold]{tool_name}[/bold]: {parsed}")
    console.print(f"[dim]Rule ID: {rule.rule_id}[/dim]")


@pref_app.command("tool-list")
def pref_tool_list():
    """List all tool preferences."""
    from vibe.preferences.tool_prefs import ToolPreferenceRegistry

    registry = ToolPreferenceRegistry()
    rules = registry.list_preferences()
    if not rules:
        console.print("[dim]No tool preferences set.[/dim]")
        return

    table = Table(title="Tool Preferences")
    table.add_column("Pattern", style="cyan")
    table.add_column("Action", style="green")
    table.add_column("Args", style="dim")
    table.add_column("Hits", style="yellow")

    for r in rules:
        table.add_row(r.pattern, r.action, str(r.action_args), str(r.hit_count))
    console.print(table)


@pref_app.command("tool-remove")
def pref_tool_remove(
    tool_name: str = typer.Argument(..., help="Tool name pattern to remove"),
):
    """Remove default arguments for a tool."""
    from vibe.preferences.tool_prefs import ToolPreferenceRegistry

    registry = ToolPreferenceRegistry()
    if registry.remove_default_args(tool_name):
        console.print(f"[green]✓[/green] Removed preferences for [bold]{tool_name}[/bold]")
    else:
        console.print(f"[yellow]No preferences found for {tool_name}[/yellow]")


@pref_app.command("prune")
def pref_prune(
    days: int = typer.Option(30, "--days", "-d", help="Remove inferred rules unused for N days"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed"),
):
    """Remove stale inferred preference rules."""
    from vibe.preferences.registry import PreferenceRegistry

    registry = PreferenceRegistry()
    if dry_run:
        # Load all policies and count what would be removed
        total = 0
        for domain in registry.list_domains():
            policy = registry.load_policy(domain)
            if policy is None:
                continue
            from datetime import datetime, timedelta, timezone

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            stale = [
                r
                for r in policy.rules
                if r.source == "inferred"
                and (r.last_used_at is None or r.last_used_at < cutoff)
                and r.hit_count > 0
            ]
            total += len(stale)
            for r in stale:
                console.print(f"[dim]Would prune: {domain}/{r.rule_id} ({r.pattern})[/dim]")
        console.print(f"[yellow]{total} rules would be pruned (dry run).[/yellow]")
    else:
        removed = registry.prune_stale(days=days)
        console.print(f"[green]✓[/green] Pruned {removed} stale rules.")


@pref_app.command("style-set")
def pref_style_set(
    key: str = typer.Argument(..., help="Style key: verbosity|plan_format|confirm_threshold|show_commands"),
    value: str = typer.Argument(..., help="Value for the key"),
):
    """Set a response style preference."""
    from vibe.preferences.style_policy import (
        ConfirmThreshold,
        PlanFormat,
        ResponseStylePolicy,
        Verbosity,
    )

    style = ResponseStylePolicy()
    if key == "verbosity":
        style.set_verbosity(Verbosity(value))
    elif key == "plan_format":
        style.set_plan_format(PlanFormat(value))
    elif key == "confirm_threshold":
        style.set_confirm_threshold(ConfirmThreshold(value))
    elif key == "show_commands":
        style.set_show_commands(value.lower() == "true")
    else:
        console.print(f"[red]Unknown style key: {key}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓[/green] Set {key} = {value}")


@pref_app.command("style-show")
def pref_style_show():
    """Show current style preferences."""
    from vibe.preferences.style_policy import ResponseStylePolicy

    style = ResponseStylePolicy()
    prompt = style.get_system_prompt_append()
    if prompt:
        console.print("[bold]Active style injections:[/bold]")
        console.print(prompt)
    else:
        console.print("[dim]No style preferences set.[/dim]")


@pref_app.command("approval-list")
def pref_approval_list():
    """List learned approval rules."""
    from vibe.preferences.approval_rules import ApprovalPolicyDB

    db = ApprovalPolicyDB()
    rules = db._policy.rules if db._policy else []
    if not rules:
        console.print("[dim]No approval rules learned.[/dim]")
        return

    table = Table(title="Approval Rules")
    table.add_column("Pattern", style="cyan")
    table.add_column("Action", style="green")
    table.add_column("Path", style="dim")
    table.add_column("Hits", style="yellow")

    for r in rules:
        path = r.action_args.get("path_pattern", "—")
        table.add_row(r.pattern, r.action, str(path), str(r.hit_count))
    console.print(table)


@pref_app.command("approval-clear")
def pref_approval_clear():
    """Clear all learned approval rules."""
    from vibe.preferences.registry import PreferenceRegistry

    PreferenceRegistry().delete_policy("approval")
    console.print("[green]✓[/green] Cleared all approval rules")


# --- Macro sub-commands (Phase D) ---

macro_app = typer.Typer(help="Macro session workflows")
app.add_typer(macro_app, name="macro")


@macro_app.command("list")
def macro_list():
    """List saved macro sessions."""
    from vibe.preferences.macro_session import MacroSessionRunner

    runner = MacroSessionRunner()
    macros = runner.list_macros()
    if not macros:
        console.print("[dim]No macros saved.[/dim]")
        return

    table = Table(title="Macro Sessions")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="dim")
    table.add_column("Trigger", style="yellow")
    table.add_column("Steps", style="green")

    for m in macros:
        table.add_row(m.name, m.description, m.trigger or "manual", str(len(m.steps)))
    console.print(table)


@macro_app.command("run")
def macro_run(
    name: str = typer.Argument(..., help="Macro name to run"),
    var: list[str] = typer.Option([], "--var", help="Variables as key=val"),
):
    """Run a macro session."""
    import asyncio

    from vibe.preferences.macro_session import MacroSessionRunner

    runner = MacroSessionRunner()
    macro = runner.load_macro(name)
    if macro is None:
        console.print(f"[red]Macro '{name}' not found.[/red]")
        raise typer.Exit(1)

    variables = {}
    for v in var:
        if "=" in v:
            k, val = v.split("=", 1)
            variables[k] = val

    console.print(f"[green]Running macro:[/green] {name}")

    async def _run():
        # Phase P4: Wire macro execution through QueryLoop for real tool use
        from vibe.core.query_loop_factory import QueryLoopFactory

        factory = QueryLoopFactory(
            base_url=DEFAULT_CONFIG.llm.base_url,
            model=DEFAULT_CONFIG.llm.default_model,
            api_key=DEFAULT_CONFIG.resolve_api_key(),
            working_dir=str(Path.cwd()),
            fallback_chain=DEFAULT_CONFIG.get_fallback_chain(),
            config=DEFAULT_CONFIG,
        )
        _ = factory.create()

        # Inject QueryLoop into runner for real execution
        runner.factory = factory

        results = runner.run(macro, variables)
        if asyncio.iscoroutine(results):
            results = await results
        console.print("\n[bold]Results:[/bold]")
        for k, v in results.items():
            console.print(f"  {k}: {v}")

    asyncio.run(_run())


@macro_app.command("create")
def macro_create(
    name: str = typer.Argument(..., help="Macro name"),
    description: str = typer.Option("", "--desc", "-d"),
):
    """Create a new macro session interactively."""
    from vibe.preferences.macro_session import MacroSession, MacroSessionRunner, MacroStep

    console.print("[dim]Enter steps (empty query to finish):[/dim]")
    steps = []
    while True:
        step_name = input("Step name: ")
        query = input("Query template: ")
        if not query:
            break
        store_as = input("Store result as (optional): ") or None
        steps.append(MacroStep(name=step_name, query=query, store_result_as=store_as))

    macro = MacroSession(name=name, description=description, steps=steps)
    runner = MacroSessionRunner()
    runner.save_macro(macro)
    console.print(f"[green]✓[/green] Saved macro '{name}' with {len(steps)} steps")


if __name__ == "__main__":
    app()

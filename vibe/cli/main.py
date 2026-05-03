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

# Phase 3.2: Session management commands
session_app = typer.Typer(help="Session management — list and resume incomplete sessions")
app.add_typer(session_app, name="session")

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
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status (draft|verified)"),
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
        status_style = "[green]verified[/green]" if p.status == "verified" else "[yellow]draft[/yellow]"
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
    console.print(Panel(
        f"[bold]{page.title}[/bold]\n"
        f"ID: {page.id}\nStatus: {page.status}\nTags: {', '.join(page.tags)}\n"
        f"Created: {page.date_created} | Updated: {page.last_updated}\n"
        f"Citations: {len(page.citations)}\n\n{page.content}",
        title=f"Wiki: {page.title}",
        border_style="cyan",
    ))


@wiki_app.command("create")
def wiki_create(
    title: str = typer.Option(..., "--title", "-t", help="Page title"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
    content: str = typer.Option("", "--content", "-c", help="Initial content (or opens $EDITOR if empty)"),
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
    console.print(f"[green]✓[/green] Created wiki page: [bold]{page.title}[/bold] (ID: {page.id[:8]})")


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
    import asyncio
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
        console.print(f"[green]✓[/green] Expired {count} draft wiki page(s) older than {days} days.")


@wiki_app.command("compile")
def wiki_compile(
    hours: int = typer.Option(24, "--hours", "-h", help="Look back N hours for sessions"),
    novelty: float = typer.Option(0.5, "--novelty", "-n", help="Novelty threshold (0.0-1.0)"),
    confidence: float = typer.Option(0.8, "--confidence", "-c", help="Confidence threshold (0.0-1.0)"),
):
    """Compile recent trace sessions into pending wiki pages for review."""
    import asyncio
    from vibe.memory.compiler import WikiCompiler
    from vibe.harness.memory.trace_store import TraceStore
    from vibe.core.query_loop_factory import QueryLoopFactory

    wiki = _get_wiki()
    trace_store = TraceStore()
    # Reuse the factory to get an LLM client for extraction
    factory = QueryLoopFactory(
        base_url=DEFAULT_CONFIG.llm.base_url,
        model=DEFAULT_CONFIG.llm.default_model,
        api_key=DEFAULT_CONFIG.resolve_api_key(),
        config=DEFAULT_CONFIG,
    )
    llm_client = factory._create_llm_client()

    compiler = WikiCompiler(
        trace_store=trace_store,
        wiki=wiki,
        llm_client=llm_client,
        config=DEFAULT_CONFIG,
    )
    summary = asyncio.run(compiler.compile_recent(
        hours=hours,
        novelty_threshold=novelty,
        confidence_threshold=confidence,
    ))
    console.print(f"[green]✓[/green] Compilation complete:")
    console.print(f"  Sessions scanned: {summary.sessions_scanned}")
    console.print(f"  Items extracted: {summary.items_extracted}")
    console.print(f"  Items approved: {summary.items_approved}")
    console.print(f"  Pages created: {summary.pages_created}")
    if summary.errors:
        console.print(f"  [yellow]Errors: {summary.errors}[/yellow]")


@wiki_app.command("review")
def wiki_review(
    auto_approve: bool = typer.Option(False, "--auto-approve", "-a", help="Approve all pending pages"),
    list_only: bool = typer.Option(False, "--list", "-l", help="List pending pages without action"),
):
    """Review pending wiki pages. Approve, reject, or list them."""
    import asyncio
    from vibe.memory.compiler import WikiCompiler
    from vibe.core.query_loop_factory import QueryLoopFactory

    wiki = _get_wiki()
    compiler = WikiCompiler(
        trace_store=None,  # Not needed for review
        wiki=wiki,
        llm_client=None,   # Not needed for review
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
            logging.getLogger("vibe.cli").debug("Failed to fetch telemetry for memory status: %s", e)

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


@session_app.command("resume")
def session_resume(
    session_id: str | None = typer.Argument(None, help="Session ID to resume (default: latest incomplete)"),
    model: str = typer.Option(DEFAULT_CONFIG.llm.default_model, "--model", "-m"),
    server: str = typer.Option(DEFAULT_CONFIG.llm.base_url, "--server", "-s"),
    api_key: str | None = typer.Option(None, "--api-key", "-k"),
    working_dir: str = typer.Option(".", "--working-dir", "-w"),
    debug: bool = typer.Option(False, "--debug", "-d", help="Print request URL and redacted headers to stderr"),
):
    """Resume an incomplete session from a checkpoint."""
    from vibe.harness.memory.session_store import SessionStore
    from vibe.core.query_loop import QueryLoop

    working_dir = str(Path(working_dir).expanduser().resolve())
    store = SessionStore()

    # Resolve session_id
    if session_id is None:
        sessions = store.list_incomplete(limit=1)
        if not sessions:
            console.print("[yellow]No incomplete sessions found. Start a new session with `vibe`.[/yellow]")
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
        console.print(f"[green]✓[/green] Resumed session [bold]{session_id[:16]}[/bold] (state: {loop.state.name}, iteration: {loop._iteration})")
        console.print("[dim]Continue the conversation. Type /exit to quit, /clear to reset.[/dim]\n")
        await interactive_mode(loop)

    try:
        asyncio.run(_run_resume())
    except ValueError as e:
        console.print(f"[red]Failed to resume: {e}[/red]")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()


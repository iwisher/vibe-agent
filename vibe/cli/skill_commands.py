"""CLI commands for vibe skill management."""
import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from vibe.harness.skills.approval import CLIApprovalGate
from vibe.harness.skills.installer import SkillInstaller
from vibe.harness.skills.parser import SkillParser
from vibe.harness.skills.validator import SkillValidator

app = typer.Typer(help="Manage vibe skills")
console = Console()


@app.command("list")
def list_skills():
    """List installed skills."""
    installer = SkillInstaller()
    skills = installer.list_installed()

    if not skills:
        console.print("[dim]No skills installed.[/dim]")
        return

    table = Table(title="Installed Skills")
    table.add_column("ID", style="cyan")
    table.add_column("Version", style="magenta")
    table.add_column("Installed", style="dim")
    table.add_column("Path", style="green")

    for skill_id, info in skills.items():
        table.add_row(
            skill_id,
            info.get("version", "?"),
            info.get("installed_at", "?")[:10],
            info.get("path", "?"),
        )

    console.print(table)


@app.command("validate")
def validate_skill(path: Path = typer.Argument(..., help="Path to skill directory")):
    """Validate a skill directory."""
    skill_file = path / "SKILL.md"
    if not skill_file.exists():
        console.print(f"[red]No SKILL.md found in {path}[/red]")
        raise typer.Exit(code=1)

    parser = SkillParser()
    validator = SkillValidator()

    try:
        skill = parser.parse_file(skill_file)
    except Exception as e:
        console.print(f"[red]Parse error: {e}[/red]")
        raise typer.Exit(code=1)

    result = validator.validate(skill, skill_dir=path)

    if result.is_valid and not result.warnings:
        console.print(f"[green]Skill '{skill.id}' is valid.[/green]")
    elif result.is_valid:
        console.print(f"[yellow]Skill '{skill.id}' is valid with warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]- {w}[/yellow]")
    else:
        console.print(f"[red]Skill '{skill.id}' has errors:[/red]")
        for r in result.risks:
            console.print(f"  [red]- {r}[/red]")
        for w in result.warnings:
            console.print(f"  [yellow]- {w}[/yellow]")
        raise typer.Exit(code=1)


@app.command("install")
def install_skill(
    source: str = typer.Argument(..., help="Git URL, local path, or tarball URL"),
    skill_id: str | None = typer.Option(None, "--id", help="Override skill ID"),
):
    """Install a skill from git, local path, or tarball."""
    installer = SkillInstaller(approval_gate=CLIApprovalGate())

    async def _install():
        if source.startswith("http") and (source.endswith(".tar.gz") or source.endswith(".tgz")):
            console.print(f"Installing from tarball: {source}")
            return await installer.install_from_tarball(source, skill_id)
        elif source.startswith("http") or source.endswith(".git"):
            console.print(f"Installing from git: {source}")
            with console.status(f"Cloning {source}..."):
                return await installer.install_from_git(source, skill_id)
        else:
            path = Path(source).expanduser().resolve()
            if not path.exists():
                console.print(f"[red]Path not found: {path}[/red]")
                raise typer.Exit(code=1)
            console.print(f"Installing from path: {path}")
            return await installer.install_from_path(path, skill_id)

    result = asyncio.run(_install())

    if result.success:
        console.print(f"[green]{result.message}[/green]")
        console.print(f"Path: {result.path}")
    else:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=1)


@app.command("uninstall")
def uninstall_skill(skill_id: str = typer.Argument(..., help="Skill ID to remove")):
    """Uninstall a skill."""
    installer = SkillInstaller()

    async def _uninstall():
        return await installer.uninstall(skill_id)

    result = asyncio.run(_uninstall())

    if result.success:
        console.print(f"[green]{result.message}[/green]")
    else:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=1)


@app.command("run")
def run_skill(
    skill_id: str = typer.Argument(..., help="Skill ID to run"),
    args: list[str] = typer.Argument(None, help="Variable assignments (key=value)"),
):
    """Run an installed skill."""
    from vibe.harness.skills.executor import SkillExecutor
    from vibe.tools.bash import BashTool

    installer = SkillInstaller()
    skills = installer.list_installed()

    if skill_id not in skills:
        console.print(f"[red]Skill '{skill_id}' not found.[/red]")
        raise typer.Exit(code=1)

    skill_path = Path(skills[skill_id]["path"])
    skill_file = skill_path / "SKILL.md"

    parser = SkillParser()
    skill = parser.parse_file(skill_file)

    # Parse variables
    variables = {}
    for arg in args or []:
        if "=" in arg:
            key, value = arg.split("=", 1)
            variables[key] = value

    bash = BashTool()
    executor = SkillExecutor(bash_tool=bash)

    async def _run():
        with console.status(f"Running skill '{skill_id}'..."):
            return await executor.execute_skill(skill, variables, skill_dir=skill_path)

    results = asyncio.run(_run())

    for i, result in enumerate(results):
        step = skill.steps[i]
        if result.success:
            console.print(f"[green]Step '{step.id}': OK[/green]")
            if result.output:
                console.print(result.output)
        else:
            console.print(f"[red]Step '{step.id}': FAILED[/red]")
            console.print(f"[red]Error: {result.error}[/red]")
            raise typer.Exit(code=1)


@app.command("create")
def create_skill(
    name: str = typer.Argument(..., help="Skill name (lowercase, hyphens)"),
    path: Path = typer.Option(Path("."), "--path", "-p", help="Where to create the skill"),
):
    """Scaffold a new skill directory with SKILL.md template."""
    # Validate name — prevent path traversal
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        console.print("[red]Invalid skill name. Use only alphanumeric, hyphens, underscores.[/red]")
        raise typer.Exit(code=1)

    skill_dir = path / name
    if skill_dir.exists():
        console.print(f"[red]Directory {skill_dir} already exists.[/red]")
        raise typer.Exit(code=1)

    skill_dir.mkdir(parents=True)
    (skill_dir / "scripts").mkdir()

    template = f'''+++
vibe_skill_version = "2.0.0"
id = "{name}"
name = "{name.replace('-', ' ').title()}"
description = "Describe what this skill does"
category = "general"
tags = []

[trigger]
patterns = []
required_tools = ["bash"]

[[steps]]
id = "step1"
description = "First step"
tool = "bash"
command = "echo 'Implement me'"

[steps.verification]
exit_code = 0

[metadata]
created_at = "2026-04-24T00:00:00Z"
auto_generated = false
+++

# {name.replace('-', ' ').title()}

## Overview
Describe what this skill does and when to use it.

## Steps

### Step 1: First Step

**Tool:** bash
**Command:** `echo 'Implement me'`

## Pitfalls

- Add known issues here

## Examples

### Example 1: Basic usage

**Input:** "Run the skill"
**Expected:** Output description
'''

    (skill_dir / "SKILL.md").write_text(template)
    console.print(f"[green]Created skill scaffold at {skill_dir}[/green]")
    console.print(f"[dim]Edit {skill_dir}/SKILL.md and add scripts to {skill_dir}/scripts/[/dim]")

#!/usr/bin/env python3
"""
Console helpers  (AutoCorp CLI - core)
======================================

All terminal output and prompting goes through here, so the look-and-feel is
consistent and the rest of the code stays free of `rich` details.
"""

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from config import APP_NAME, APP_VERSION

console = Console()

_SEV = {
    "info": "cyan",
    "ok": "green",
    "warn": "yellow",
    "error": "red",
    "muted": "grey50",
}


# --------------------------------------------------------------------------- #
# Basic messages
# --------------------------------------------------------------------------- #
def info(msg: str) -> None:
    console.print(f"[cyan]›[/cyan] {msg}")


def success(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def warn(msg: str) -> None:
    console.print(f"[yellow]![/yellow] {msg}")


def error(msg: str) -> None:
    console.print(f"[red]✗[/red] {msg}")


def muted(msg: str) -> None:
    console.print(f"[grey50]{msg}[/grey50]")


def rule(title: str = "") -> None:
    console.rule(f"[bold]{title}[/bold]" if title else "")


def banner() -> None:
    console.print(
        Panel.fit(
            Text(f"{APP_NAME}", style="bold cyan")
            + Text(f"  v{APP_VERSION}\n", style="grey50")
            + Text("Local AI coding assistant — plan · build · test · explain",
                   style="white"),
            border_style="cyan",
        )
    )


# --------------------------------------------------------------------------- #
# Structured displays
# --------------------------------------------------------------------------- #
def show_plan(plan: dict) -> None:
    """Render a build plan: summary, steps, files, test command.

    Planner v2 adds project_type, build_order, and success_criteria; all are
    optional here so v1-shaped plans still render."""
    name = plan.get("project_name", "project")
    lang = plan.get("language", "")
    ptype = plan.get("project_type", "")
    summary = plan.get("summary", "")
    tag = " · ".join(t for t in (lang, ptype) if t)
    console.print(
        Panel(
            Text(summary or "(no summary)", style="white"),
            title=f"[bold]Plan — {name}[/bold]" + (f"  [grey50]({tag})[/grey50]" if tag else ""),
            border_style="cyan",
        )
    )

    steps = plan.get("steps") or []
    if steps:
        t = Table(title="Steps", show_header=True, header_style="bold", box=None)
        t.add_column("#", justify="right", style="grey50", width=3)
        t.add_column("Step")
        for i, s in enumerate(steps, 1):
            t.add_row(str(i), str(s))
        console.print(t)

    files = plan.get("files") or []
    if files:
        t = Table(title="Files", show_header=True, header_style="bold", box=None)
        t.add_column("Path", style="cyan")
        t.add_column("Purpose")
        for f in files:
            if isinstance(f, dict):
                t.add_row(f.get("path", "?"), f.get("purpose", ""))
            else:
                t.add_row(str(f), "")
        console.print(t)

    build_order = plan.get("build_order") or []
    if build_order:
        console.print(f"[grey50]Build order:[/grey50] " + " → ".join(str(p) for p in build_order))

    tc = plan.get("test_command")
    if tc:
        console.print(f"[grey50]Test command:[/grey50] [bold]{tc}[/bold]")

    criteria = plan.get("success_criteria") or []
    if criteria:
        t = Table(title="Success criteria", show_header=False, box=None)
        t.add_column("", style="green")
        for c in criteria:
            t.add_row(f"✓ {c}")
        console.print(t)


def show_code(path: str, content: str, lexer: str = "python") -> None:
    """Syntax-highlighted code preview."""
    console.print(
        Panel(
            Syntax(content, lexer, theme="ansi_dark", line_numbers=True, word_wrap=False),
            title=f"[cyan]{path}[/cyan]",
            border_style="grey50",
        )
    )


def show_panel(title: str, body: str, style: str = "cyan") -> None:
    console.print(Panel(Text(body), title=f"[bold]{title}[/bold]", border_style=style))


def show_table(title: str, columns: list, rows: list) -> None:
    t = Table(title=title, show_header=True, header_style="bold")
    for col in columns:
        t.add_column(col)
    for row in rows:
        t.add_row(*[str(c) for c in row])
    console.print(t)


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #
def confirm(question: str, default: bool = True) -> bool:
    """Yes/No prompt. Falls back to the default if there is no interactive TTY."""
    import sys
    if not sys.stdin or not sys.stdin.isatty():
        return default
    try:
        return Confirm.ask(question, default=default, console=console)
    except (EOFError, KeyboardInterrupt):
        return False


def ask(question: str, default: str = "") -> str:
    try:
        return Prompt.ask(question, default=default, console=console)
    except (EOFError, KeyboardInterrupt):
        return ""

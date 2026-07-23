#!/usr/bin/env python3
"""
AutoCorp CLI  -  local AI coding assistant
==========================================

Terminal-first. Powered by local Ollama (llama3.2). Plans, builds, tests, and
explains code, and learns from past builds.

Usage:
    python autocorp.py                      # interactive REPL
    python autocorp.py build "<request>"    # plan -> build -> test
    python autocorp.py plan  "<request>"    # plan only, writes nothing
    python autocorp.py test  [workspace]    # run tests on an existing build
    python autocorp.py explain <file>       # explain a source file
    python autocorp.py memory               # show what it has learned
    python autocorp.py scan                 # read-only repository scan
    python autocorp.py analyze              # read-only project architecture analysis

    --auto    skip confirmations (uses the allow-all gate)

To hand command approval to Agent Watchdog later, swap the gate in `_make_gate`.
"""

import argparse
import os
import sys

# Make sure sibling packages import cleanly regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from core import console, llm
from core.orchestrator import Session
from memory import store
from safety.gate import AllowAllGate, ConfirmGate
from safety.watchdog_gate import WatchdogGate
from brains import analyzer, engine_registry, scanner


def _make_gate(auto: bool = False, watchdog: bool = False):
    if auto:
        return AllowAllGate()
    if watchdog:
        return WatchdogGate()
    return ConfirmGate()


def _make_engine(name: str = "local"):
    """Select the code-generation engine via the registry (single source of
    truth). Default is the local Ollama engine. Raises ValueError on an unknown
    name, listing the valid engines."""
    return engine_registry.create(name)


def _require_ollama() -> bool:
    ok, msg = llm.check_ollama()
    (console.success if ok else console.error)(msg)
    return ok


# --------------------------------------------------------------------------- #
# Subcommand handlers
# --------------------------------------------------------------------------- #
def cmd_build(args) -> int:
    if not _require_ollama():
        return 1
    engine_name = getattr(args, "engine", "local")
    routing = engine_name == "auto"
    session = Session(_make_gate(args.auto, args.watchdog), assume_yes=args.auto,
                      review=getattr(args, "review", False), route=routing,
                      accept=getattr(args, "accept", False) or getattr(args, "accept_strict", False),
                      accept_strict=getattr(args, "accept_strict", False),
                      tester_engine=getattr(args, "tester_engine", "local"),
                      self_heal=getattr(args, "self_heal", False))
    if routing:
        # The router selects builder.engine after planning; don't pre-set it.
        console.muted("Engine: auto (rule-based routing)")
    else:
        # Engine selection (default local). The frozen orchestrator is untouched
        # - we swap the engine on the already-constructed builder.
        session.builder.engine = _make_engine(engine_name)
        console.muted(f"Engine: {session.builder.engine.name}")
    result = session.run(args.request)
    return 0 if result.get("status") in ("passed",) else 1


def cmd_plan(args) -> int:
    if not _require_ollama():
        return 1
    # Planning writes nothing, so the gate is irrelevant here.
    session = Session(AllowAllGate())
    lessons = store.recall_lessons(args.request)
    if lessons:
        console.muted(f"Recalled {len(lessons)} relevant lesson(s) from memory.")
    try:
        plan = session.planner.plan(args.request, store.format_lessons_for_prompt(lessons))
    except llm.OllamaError as e:
        console.error(f"Planning failed: {e}")
        return 1
    console.show_plan(plan)
    console.muted("(plan only — no files were written)")
    return 0


def cmd_test(args) -> int:
    if not _require_ollama():
        return 1
    workspace = args.workspace or os.getcwd()
    if not os.path.isdir(workspace):
        console.error(f"No such workspace: {workspace}")
        return 1
    session = Session(_make_gate(args.auto, args.watchdog))
    # Minimal plan so the tester can infer a command.
    result = session.tester.test(workspace, {"test_command": args.command or ""})
    return 0 if result.ok else 1


def cmd_explain(args) -> int:
    if not _require_ollama():
        return 1
    session = Session(AllowAllGate())
    session.explain(args.file)
    return 0


def cmd_memory(args) -> int:
    store.init_db()
    s = store.stats()
    console.show_panel(
        "Memory",
        f"Builds: {s['builds']}   Lessons: {s['lessons']}   "
        f"Successes: {s['successes']}   Fixes: {s['fixes']}",
        "cyan",
    )
    builds = store.recent_builds(10)
    if builds:
        console.show_table(
            "Recent builds",
            ["When", "Project", "Status", "Request"],
            [[b["ts"][:19].replace("T", " "), b["project_name"], b["status"],
              (b["request"] or "")[:50]] for b in builds],
        )
    lessons = store.recent_lessons(15)
    if lessons:
        console.show_table(
            "Lessons learned",
            ["Kind", "Title", "Solution"],
            [[l["kind"], (l["title"] or "")[:40], (l["solution"] or "")[:50]]
             for l in lessons],
        )
    if not builds and not lessons:
        console.muted("Memory is empty — build something first.")
    return 0


def cmd_scan(args) -> int:
    """Read-only repository scan: git status, Python version, file counts, and
    code-health markers. Writes nothing - see brains/scanner.py."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    result = scanner.run_scan(repo_root)
    console.rule("Repository Scan")
    print(f"Repository:       {result.repo_path}")
    print(f"Branch:            {result.branch}")
    print(f"Working Tree:      {result.working_tree}")
    print(f"Python Version:    {result.python_version}")
    print(f"Python Files:      {result.python_file_count}")
    print(f"Test Files:        {result.test_file_count}")
    print(f"TODO:              {result.todo_count}")
    print(f"FIXME:             {result.fixme_count}")
    print(f"pass Statements:   {result.pass_count}")
    print(f"NotImplementedError: {result.not_implemented_count}")
    return 0


def cmd_analyze(args) -> int:
    """Read-only project architecture analysis: project type, entry points,
    test framework, layout, and health. Writes nothing - see
    brains/analyzer.py."""
    repo_root = os.path.dirname(os.path.abspath(__file__))
    result = analyzer.run_analysis(repo_root)

    print("Project Analysis")
    print("================")
    print()
    print("Project Type:")
    print(result.project_type)
    print()
    print("Primary Language:")
    print(result.primary_language)
    print()
    print("Entry Points:")
    print("\n".join(result.entry_points) or "(none found)")
    print()
    print("Test Framework:")
    print(result.test_framework)
    print()
    print("Dependency Files:")
    print("\n".join(result.dependency_files) or "(none found)")
    print()
    print("Python Files:")
    print(result.python_file_count)
    print()
    print("Largest Package:")
    print(result.largest_package or "(none)")
    print()
    print("Largest Module:")
    print(result.largest_module or "(none)")
    print()
    print("Top Directories:")
    for d in result.top_directories[:5]:
        print(f"{d.name} ({d.python_files} files, {d.python_lines} lines)")
    print()
    print("Quality Indicators")
    print("------------------")
    print(f"TODO: {result.todo_count}")
    print(f"FIXME: {result.fixme_count}")
    print(f"pass Statements: {result.pass_count}")
    print(f"NotImplementedError: {result.not_implemented_count}")
    print()
    print("Overall Health:")
    print(result.overall_health)
    print()
    print("Confidence:")
    print(f"{result.confidence}%")
    return 0


def repl(auto: bool, watchdog: bool = False) -> int:
    console.banner()
    if not _require_ollama():
        return 1
    session = Session(_make_gate(auto, watchdog), assume_yes=auto)
    console.muted("Type a request to build, 'explain <file>', 'memory', or 'quit'.")
    while True:
        request = console.ask("\n[bold cyan]autocorp[/bold cyan]")
        cmd = request.strip()
        if not cmd:
            continue
        if cmd.lower() in ("quit", "exit", "q"):
            console.muted("Bye.")
            return 0
        if cmd.lower() == "memory":
            cmd_memory(None)
            continue
        if cmd.lower().startswith("explain "):
            session.explain(cmd[len("explain "):].strip())
            continue
        session.run(cmd)


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autocorp",
        description=f"{config.APP_NAME} v{config.APP_VERSION} — local AI coding assistant.",
    )
    gate_group = p.add_mutually_exclusive_group()
    gate_group.add_argument("--auto", action="store_true",
                            help="skip confirmations (use the allow-all gate)")
    gate_group.add_argument("--watchdog", action="store_true",
                            help="gate commands through Agent Watchdog "
                                 "(falls back to confirm if it isn't available)")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("build", help="plan, build, and test from a request")
    sp.add_argument("request", help="what to build")
    sp.add_argument("--engine",
                    choices=engine_registry.available_engines() + ["auto"],
                    default="local",
                    help="code-generation engine, or 'auto' for rule-based "
                         "routing (default: local)")
    sp.add_argument("--tester-engine",
                    choices=engine_registry.available_engines(),
                    default="local",
                    help="engine used for self-heal repairs (Tester/repair "
                         "chain); 'deepseek' or 'claude' only when explicitly "
                         "selected (default: local)")
    sp.add_argument("--review", action="store_true",
                    help="run a non-blocking static code review before tests")
    sp.add_argument("--accept", action="store_true",
                    help="evaluate the team profile's acceptance criteria "
                         "(advisory; does not change build status)")
    sp.add_argument("--accept-strict", action="store_true",
                    help="enforce acceptance criteria: an unaccepted build is "
                         "reported as 'accept_failed'")
    sp.add_argument("--self-heal", action="store_true",
                    help="enable the DS6 self-heal repair cycle on an "
                         "unaccepted build (uses the --tester-engine; default off)")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("plan", help="show a build plan (writes nothing)")
    sp.add_argument("request", help="what to plan")
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser("test", help="run tests on an existing workspace")
    sp.add_argument("workspace", nargs="?", help="path to the project (default: cwd)")
    sp.add_argument("-c", "--command", help="explicit test command to run")
    sp.set_defaults(func=cmd_test)

    sp = sub.add_parser("explain", help="explain a source file")
    sp.add_argument("file", help="path to the file")
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("memory", help="show stored builds and lessons")
    sp.set_defaults(func=cmd_memory)

    sp = sub.add_parser("scan", help="read-only repository scan (git, files, TODO/FIXME)")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("analyze", help="read-only project architecture analysis")
    sp.set_defaults(func=cmd_analyze)

    return p


def main() -> int:
    config.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        return repl(args.auto, args.watchdog)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

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
from brains import analyzer, engine_registry, project_planner, repair_executor, repair_proposal, scanner, workspace


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


def _resolve_repo(args) -> str:
    """Resolve the --repo argument and print a workspace header when an
    external target is requested. Returns the resolved repository root."""
    default_path = os.path.dirname(os.path.abspath(__file__))
    repo_arg = getattr(args, "repo", None)

    resolution = workspace.resolve_workspace(repo_arg, default_path)

    if not resolution.is_git_repository:
        print("Workspace Error")
        print("===============")
        print()
        if resolution.requested_path:
            print(f"Requested Path:  {resolution.requested_path}")
        print(f"Resolved Path:   {resolution.resolved_path}")
        print()
        print("Reason:")
        for b in resolution.blockers:
            print(f"  - {b}")
        print()
        print("No Changes Made: Yes")
        raise SystemExit(1)

    if not resolution.is_default_repository:
        print("Workspace")
        print("=========")
        print()
        print(f"Requested Path:      {resolution.requested_path}")
        print(f"Resolved Repository: {resolution.repo_root}")
        print()
        print("Git Repository: Yes")
        print("Mode:            External Target")
        print()
    else:
        print("Mode: AutoCorp Default")
        print()

    return resolution.repo_root


def cmd_scan(args) -> int:
    """Read-only repository scan: git status, Python version, file counts, and
    code-health markers. Writes nothing - see brains/scanner.py."""
    repo_root = _resolve_repo(args)
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
    repo_root = _resolve_repo(args)
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


def cmd_plan_project(args) -> int:
    """Read-only project action planner: converts scanner + analyzer
    evidence into a deterministic, prioritized action plan. Never writes,
    never calls a model - see brains/project_planner.py."""
    repo_root = _resolve_repo(args)
    plan = project_planner.run_project_plan(repo_root)

    print("Project Action Plan")
    print("===================")
    print()
    print("Repository:")
    print(plan.repo_path)
    print()
    print("Project Type:")
    print(plan.project_type)
    print()
    print("Overall Health:")
    print(plan.overall_health)
    print()
    print("Summary:")
    print(plan.summary)
    print()
    print("Blockers:")
    if plan.blockers:
        for b in plan.blockers:
            print(f"- {b}")
    else:
        print("(none)")
    print()
    if plan.actions:
        print("Recommended Actions")
        print("-------------------")
        print()
        for idx, action in enumerate(plan.actions, 1):
            print(f"{idx}. [{action.priority.upper()}] {action.title}")
            print(f"   Category: {action.category}")
            print(f"   Reason: {action.reason}")
            if action.evidence:
                print("   Evidence:")
                for ev in action.evidence:
                    print(f"   - {ev}")
            print(f"   Next Step: {action.recommended_next_step}")
            if getattr(action, "affected_paths", ()):
                print("   Affected Paths:")
                for p in action.affected_paths:
                    print(f"   - {p}")
            print(f"   Safe to Automate: {'Yes' if action.safe_to_automate else 'No'}")
            print(f"   Confidence: {action.confidence}%")
            print()
    else:
        print("Recommended Actions")
        print("-------------------")
        print()
        print("(none)")
        print()
    print(f"Plan confidence: {plan.confidence}%")
    return 0


def cmd_repair(args) -> int:
    """Safe repair executor: builds and optionally executes a repair plan
    for a Phase 1C action by ID. Never writes without --approve. Never
    commits, never pushes, never calls a model."""
    repo_root = _resolve_repo(args)
    approved = getattr(args, "approve", False) and not getattr(args, "dry_run", True)
    action_id = args.action

    plan = repair_executor.build_repair_plan(repo_root, action_id)

    print("Safe Repair Plan")
    print("================")
    print()
    print(f"Repository:  {plan.repo_path}")
    print(f"Action ID:   {plan.action_id}")
    print(f"Action:      {plan.action_title}")
    print(f"Priority:    {plan.priority}")
    print(f"Category:    {plan.category}")
    print(f"Mode:        {'APPROVED EXECUTION' if approved else 'DRY RUN'}")
    print()
    print("Summary:")
    print(plan.summary)
    print()
    if plan.operations:
        print("Operations:")
        for idx, op in enumerate(plan.operations, 1):
            print(f"  {idx}. [{op.operation_type}] {op.description}")
            print(f"     Path: {op.path}")
            print(f"     Safe to apply: {'Yes' if op.safe_to_apply else 'No'}")
        print()
    if plan.validation_commands:
        print("Validation:")
        for cmd in plan.validation_commands:
            print(f"  - {cmd}")
        print()
    if plan.blockers:
        print("Blockers:")
        for b in plan.blockers:
            print(f"  - {b}")
        print()
    print(f"Can Execute:  {'Yes' if plan.can_execute else 'No'}")
    print(f"No Changes Made: {'Yes' if not approved else 'N/A (approved)'}")
    print()

    action_not_found = not plan.action_title

    if not approved:
        print("Dry-run completed. No changes were made.")
        return 1 if action_not_found else 0

    result = repair_executor.execute_repair_plan(plan, approved=True)

    print("Repair Result")
    print("=============")
    print()
    print(f"Status:          {result.status}")
    print(f"Changed Paths:")
    if result.changed_paths:
        for p in result.changed_paths:
            print(f"  - {p}")
    else:
        print("  (none)")
    print(f"Validation Passed: {'Yes' if result.validation_passed else 'No'}")
    print(f"Rolled Back:      {'Yes' if result.rolled_back else 'No'}")
    print(f"Message:          {result.message}")

    if result.status in ("completed", "dry_run"):
        return 0
    return 1


def cmd_propose_repair(args) -> int:
    """AI repair proposal engine: generates a structured, review-only
    repair proposal for a Phase 1C action. Never applies changes, never
    commits, never pushes."""
    repo_root = _resolve_repo(args)
    action_id = args.action
    provider = getattr(args, "provider", None) or "local"
    model = getattr(args, "model", None)
    output_path = getattr(args, "output", None)
    overwrite = getattr(args, "overwrite", False)

    proposal = repair_proposal.build_repair_proposal(
        repo_root, action_id, provider=provider, model=model,
    )

    print("AI Repair Proposal")
    print("==================")
    print()
    print(f"Workspace:   {proposal.repo_path}")
    print(f"Action ID:   {proposal.action_id}")
    print(f"Action:      {proposal.action_title}")
    print(f"Provider:    {proposal.provider}")
    print(f"Model:       {proposal.model}")
    print()
    print("Summary:")
    print(proposal.summary or "(none)")
    print()
    print("Reasoning Summary:")
    print(proposal.reasoning_summary or "(none)")
    print()

    if proposal.files:
        print("Files")
        print("-----")
        print()
        for idx, f in enumerate(proposal.files, 1):
            print(f"  {idx}. {f.path}")
            print(f"     Purpose: {f.purpose}")
            print(f"     Current SHA-256: {f.current_sha256}")
            print(f"     Proposed Change: {f.proposed_change_summary}")
            if f.proposed_patch:
                print("     Patch:")
                for pline in f.proposed_patch.splitlines():
                    print(f"       {pline}")
            print()

    if proposal.validation_plan:
        print("Validation Plan:")
        for v in proposal.validation_plan:
            print(f"  - {v}")
        print()

    if proposal.risks:
        print("Risks:")
        for r in proposal.risks:
            print(f"  - {r}")
        print()

    if proposal.blockers:
        print("Blockers:")
        for b in proposal.blockers:
            print(f"  - {b}")
        print()

    print(f"Safe to Apply:   {'Yes' if proposal.safe_to_apply else 'No'}")
    print(f"Confidence:      {proposal.confidence}%")
    print()
    print(f"Redactions:      {proposal.redactions}")
    if proposal.redaction_summary:
        print(f"                 {proposal.redaction_summary}")
    print()
    print("No Changes Made: Yes")

    if proposal.provider_error:
        print()
        print(f"Provider Error: {proposal.provider_error}")
        return 1

    if proposal.blockers:
        return 1

    if output_path:
        try:
            saved = repair_proposal.write_repair_proposal(
                proposal, output_path, overwrite=overwrite,
            )
            print(f"Proposal saved to: {saved}")
        except FileExistsError:
            print(f"Output file already exists: {output_path}")
            print("Use --overwrite to replace.")
            return 1
        except (ValueError, OSError) as exc:
            print(f"Failed to write output: {exc}")
            return 1

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
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("analyze", help="read-only project architecture analysis")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("plan-project", help="read-only project action planner")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.set_defaults(func=cmd_plan_project)

    sp = sub.add_parser("repair", help="safe repair executor for Phase 1C actions")
    sp.add_argument("--action", required=True, metavar="ACTION_ID",
                    help="Phase 1C action ID to repair")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--dry-run", action="store_true",
                    help="build and print the repair plan without making changes")
    sp.add_argument("--approve", action="store_true",
                    help="apply the repair (required for any file changes)")
    sp.set_defaults(func=cmd_repair)

    sp = sub.add_parser("propose-repair", help="AI repair proposal engine (review-only)")
    sp.add_argument("--action", required=True, metavar="ACTION_ID",
                    help="Phase 1C action ID to generate a proposal for")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--provider", default=None, metavar="PROVIDER",
                    help="AI provider: local (ollama), deepseek, claude")
    sp.add_argument("--model", default=None, metavar="MODEL",
                    help="override default model name")
    sp.add_argument("--output", default=None, metavar="PATH",
                    help="absolute path for proposal JSON output")
    sp.add_argument("--overwrite", action="store_true",
                    help="overwrite existing output file")
    sp.set_defaults(func=cmd_propose_repair)

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

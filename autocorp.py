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
from autocorp_testing import reporting as fast_pytest_reporting
from brains import analyzer, chat, discovery, engine_registry, fast_pytest_engine, guided_clonecast_episode, live_inspector, live_readiness, live_test, manager, project_planner, quick_podcast, repair_executor, repair_proposal, scanner, workflow_test, workspace


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


def cmd_episode_build(args) -> int:
    try:
        if args.approve:
            session = guided_clonecast_episode.approve_for_publishing(args.approve, owner=args.owner)
            print("PUBLISHING ELIGIBLE")
            print("Reason: Owner approved the completed episode")
            print(f"Session: {session.session_id}")
            return 0
        if args.reject:
            session = guided_clonecast_episode.reject_after_review(args.reject, reason=args.reason)
            print("PUBLISHING LOCKED")
            print("Reason: Owner review not completed")
            print(f"Session: {session.session_id}")
            return 0
        if args.regenerate_section:
            if not args.resume:
                print("Episode Build Error: --resume is required with --regenerate-section", file=sys.stderr)
                return 2
            session = guided_clonecast_episode.regenerate_section(args.resume, args.regenerate_section)
            print(f"Regeneration recorded for section: {args.regenerate_section}")
            print(f"Session: {session.session_id}")
            return 0
        session = guided_clonecast_episode.run_guided_episode_build(args.repo, resume=args.resume)
        print(f"Session saved: {guided_clonecast_episode.session_path(session.session_id)}")
        return 0
    except guided_clonecast_episode.EpisodeBuildError as exc:
        print(f"Episode Build Error: {exc}", file=sys.stderr)
        return 2


def cmd_test_plan(args) -> int:
    """Fast Pytest Engine: read-only test plan. Never runs tests, never
    writes to the target repository - see brains/fast_pytest_engine.py."""
    try:
        plan = fast_pytest_engine.test_plan(args.repo, feature=getattr(args, "feature", None),
                                            base_branch=getattr(args, "base_branch", None))
    except fast_pytest_engine.EngineError as exc:
        print(f"Test Plan Error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(fast_pytest_reporting.to_json(plan))
    else:
        print(fast_pytest_reporting.render_plan(plan))
    return 0


def cmd_test_fast(args) -> int:
    """Fast Pytest Engine: immediate developer feedback. Never grants
    production approval - see brains/fast_pytest_engine.py."""
    try:
        report = fast_pytest_engine.test_fast(args.repo)
    except fast_pytest_engine.EngineError as exc:
        print(f"Test Fast Error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(fast_pytest_reporting.to_json(report))
    else:
        print(fast_pytest_reporting.render_report(report))
    return report.exit_code


def cmd_test_focused(args) -> int:
    """Fast Pytest Engine: verifies one feature/subsystem. Never grants
    production approval, never silently becomes FULL."""
    try:
        report = fast_pytest_engine.test_focused(args.repo, feature=getattr(args, "feature", None),
                                                  path=getattr(args, "path", None),
                                                  node_id=getattr(args, "node_id", None))
    except fast_pytest_engine.EngineError as exc:
        print(f"Test Focused Error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(fast_pytest_reporting.to_json(report))
    else:
        print(fast_pytest_reporting.render_report(report))
    return report.exit_code


def cmd_test_full(args) -> int:
    """Fast Pytest Engine: the repository's own complete strict
    verification, run unmodified. The only mode that can contribute to
    production approval."""
    try:
        report = fast_pytest_engine.test_full(args.repo)
    except fast_pytest_engine.EngineError as exc:
        print(f"Test Full Error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(fast_pytest_reporting.to_json(report))
    else:
        print(fast_pytest_reporting.render_report(report))
    return report.exit_code


def _resolve_repo(args, quiet: bool = False) -> str:
    """Resolve the --repo argument and print a workspace header when an
    external target is requested. Returns the resolved repository root."""
    default_path = os.path.dirname(os.path.abspath(__file__))
    repo_arg = getattr(args, "repo", None)

    resolution = workspace.resolve_workspace(repo_arg, default_path)

    if not resolution.is_git_repository:
        if quiet:
            reason = "; ".join(resolution.blockers) or "Not a Git repository"
            raise SystemExit(f"Workspace Error: {reason}")
        else:
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

    if quiet:
        return resolution.repo_root

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
    raw_provider = getattr(args, "provider", None) or "local"
    _PROVIDER_ALIASES = {"ollama": "local"}
    provider = _PROVIDER_ALIASES.get(raw_provider, raw_provider)
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


def cmd_live_readiness(args) -> int:
    """Live application readiness scanner: static-only diagnostic that
    inspects a repository and reports launch and workflow readiness.
    Never edits, never executes target code."""
    repo_root = _resolve_repo(args)
    report = live_readiness.run_live_readiness(repo_root)

    print("Live Application Readiness")
    print("==========================")
    print()
    print(f"Repository:        {report.repo_path}")
    print(f"Project Type:      {report.project_type}")
    print(f"Overall Status:    {report.overall_status}")
    print()

    if report.launch_candidates:
        print("Launch Candidates")
        print("-----------------")
        for lc in report.launch_candidates:
            print(f"  {lc}")
        print()

    if report.health_endpoints:
        print("Health Endpoints")
        print("----------------")
        for he in report.health_endpoints:
            print(f"  {he}")
        print()

    if report.workflow_stages:
        print("Workflow Stages")
        print("---------------")
        for idx, stage in enumerate(report.workflow_stages, 1):
            status = stage.get("status", "unknown")
            print(f"  {idx}. {stage.get('stage', '?')}")
            print(f"     Status: {status}")
            for ev in stage.get("evidence", [])[:3]:
                print(f"     Evidence: {ev}")
        print()

    if report.checks:
        print("Readiness Checks")
        print("----------------")
        print()
        cat_order = {"fail": "[FAIL]", "blocked": "[BLOCKED]", "warning": "[WARN]",
                      "unknown": "[UNK]", "pass": "[PASS]"}
        for idx, ch in enumerate(report.checks, 1):
            tag = cat_order.get(ch.status, "[???]")
            print(f"  {idx}. {tag} {ch.title}")
            print(f"     Category: {ch.category}")
            print(f"     Reason: {ch.reason}")
            if ch.evidence:
                for ev in ch.evidence[:5]:
                    print(f"     Evidence: {ev}")
            if ch.affected_paths:
                for p in ch.affected_paths[:5]:
                    print(f"     Path: {p}")
            print(f"     Confidence: {ch.confidence}%")
            print()

    if report.blockers:
        print("Blockers")
        print("--------")
        for b in report.blockers:
            print(f"  - {b}")
        print()

    print("Static Inspection Only: Yes")
    print("No Changes Made: Yes")
    print()
    print(f"Report confidence: {report.confidence}%")
    return 0


def cmd_live_test(args) -> int:
    """Controlled live application test: launch the target app, check
    health endpoints and service readiness, then cleanly shut down.
    Never modifies the target repository."""
    repo_root = _resolve_repo(args)
    timeout = getattr(args, "timeout", 30)
    port = getattr(args, "port", 8000)
    report = live_test.run_live_test(repo_root, timeout=timeout, port=port)

    print("Live Application Test")
    print("====================")
    print()
    print(f"Repository:     {report.repo_path}")
    print(f"Project Type:   {report.project_type}")
    print(f"Overall Status: {report.overall_status}")
    print(f"Duration:       {report.duration_seconds:.1f}s")
    print()

    for stage in report.stages:
        tag = {"PREFLIGHT_PASSED": "[OK]", "PREFLIGHT_FAILED": "[FAIL]",
               "LAUNCH_PLAN_READY": "[OK]", "STARTUP_SUCCEEDED": "[OK]",
               "STARTUP_FAILED": "[FAIL]",
               "HEALTH_CHECKS_COMPLETE": "[OK]",
               "SERVICE_CHECK_COMPLETE": "[OK]",
               "DRY_RUN_DISCOVERY_COMPLETE": "[OK]",
               "SHUTDOWN_COMPLETE": "[OK]",
               "CHANGE_VERIFICATION_COMPLETE": "[OK]"}.get(stage.status, "[--]")
        print(f"{tag} Stage {stage.stage}: {stage.title}  ({stage.status})")
        for f in stage.findings:
            print(f"    {f}")
        for e in stage.errors:
            print(f"    ERROR: {e}")
        print()

    if report.health_results:
        print("Health Checks")
        print("-------------")
        for hr in report.health_results:
            tag = "[PASS]" if hr.passed else "[FAIL]"
            print(f"  {tag} {hr.method} {hr.url}")
            print(f"       Status: {hr.status_code}  Time: {hr.response_time_ms:.0f}ms")
            if hr.error:
                print(f"       Error: {hr.error}")
            if hr.body_preview:
                print(f"       Body: {hr.body_preview[:120]}")
        print()

    if report.dependency_status:
        print("Service Readiness")
        print("-----------------")
        for ds in report.dependency_status:
            print(f"  {ds['service']}: {ds['state']}")
        print()

    if report.dry_run_capabilities:
        print("Dry-Run Capabilities")
        print("--------------------")
        for dc in report.dry_run_capabilities:
            print(f"  {dc['stage']}: {'safe' if dc.get('safe') else 'unsafe'}")
            if dc.get("note"):
                print(f"    {dc['note']}")
        print()

    if report.before_sha256:
        print("Database Integrity")
        print("------------------")
        for path, h in report.before_sha256.items():
            after = report.after_sha256.get(path, "")
            unchanged = "Yes" if after == h else "NO - DATABASE WAS MODIFIED"
            print(f"  {path}: unchanged={unchanged}")
        print()

    return report.exit_code


_EXTERNAL_DEPENDENCY_OWNERSHIP = "MISSING_EXTERNAL_MODEL_OR_DEPENDENCY"


def _render_workflow_report(report) -> str:
    """Render the full disposable-workflow report as text. Used for both
    terminal output and the saved report file, so the two never drift apart.
    Shared by Phase 1X (audio production) and Phase 1Y (publishing
    validation, which reuses this same report shape with additional
    stages/artifacts/findings appended)."""
    is_publishing = getattr(report, "include_publishing", False) or report.publishing_readiness != "NOT_RUN"
    lines = []
    p = lines.append

    if is_publishing:
        p("Phase 1Y - CloneCast Production Publishing Validation")
        p("=======================================================")
    else:
        p("Phase 1X - CloneCast Disposable Workflow Validation")
        p("=====================================================")
    p("")
    p(f"Repository:       {report.repo_path}")
    p(f"Disposable Root:  {report.disposable_root}")
    p(f"Production DB:    {report.production_db_path}")
    p(f"Overall Status:   {report.overall_status}")
    p(f"Success:          {'Yes' if getattr(report, 'success', False) else 'No'}")
    p(f"Failure Reason:   {getattr(report, 'failure_reason', '') or '(none)'}")
    p(f"Workflow Stage:   {getattr(report, 'workflow_stage', 'UNKNOWN')}")
    p(f"Runtime:          {report.duration:.1f}s")
    p(f"Duration:         {report.duration:.1f}s")
    if report.cleanup_attempted:
        cleanup_status = "REMOVED" if report.cleanup_removed else "FAILED"
    else:
        cleanup_status = "NOT_CREATED"
    p(f"Disposable Cleanup Status: {cleanup_status}")
    p(f"Repository Unchanged: {'Yes' if getattr(report, 'repository_unchanged', False) else 'No'}")
    p(f"Verification Summary: {getattr(report, 'verification_summary', '(not recorded)')}")
    p(f"Recommended Next Action: {getattr(report, 'recommended_next_action', '(not recorded)')}")
    p("")

    p("Workflow Stages")
    p("----------------")
    pass_count = sum(1 for s in report.stages if s.status == "PASS")
    fail_count = sum(1 for s in report.stages if s.status == "FAIL")
    p(f"  {pass_count} passed, {fail_count} failed, {len(report.stages)} total")
    p("")
    for s in report.stages:
        tag = "[PASS]" if s.status == "PASS" else "[FAIL]" if s.status == "FAIL" else "[SKIP]"
        p(f"  {tag} Stage {s.number}: {s.stage}  ({s.duration:.1f}s)")
        if s.route:
            p(f"       Route: {s.method} {s.route}")
        if s.content_type:
            p(f"       Content-Type: {s.content_type}")
        if s.operation_id:
            p(f"       Operation: {s.operation_id}")
        if s.validation_errors:
            p("       Validation errors:")
            for ve in s.validation_errors[:5]:
                p(f"         {ve}")
        if s.extracted_ids:
            for k, v in s.extracted_ids.items():
                p(f"       ID: {k}={v}")
        if s.flash_messages:
            p(f"       Flash: {dict(s.flash_messages)}")
        if s.redirect_url:
            p(f"       Redirect: {s.redirect_url[:200]}")
        if s.failure_ownership:
            p(f"       Ownership: {s.failure_ownership}")
        if s.request_body:
            p(f"       Body: {s.request_body[:200]}")
        if s.response_code:
            p(f"       Response: {s.response_code}")
        for ev in s.evidence:
            p(f"       {ev}")
        if s.failure_reason:
            p(f"       FAILURE: {s.failure_reason}")
        p("")

    if report.first_failure:
        p(f"First Failure: {report.first_failure}")
    else:
        p("First Failure: (none)")

    p("")
    p("Artifact Table")
    p("--------------")
    if report.artifacts:
        for a in report.artifacts:
            p(f"  [{a.kind}]")
            p(f"    Path:              {a.path or '(none)'}")
            p(f"    Size bytes:        {a.size_bytes}")
            p(f"    Duration seconds:  {a.duration_seconds}")
            p(f"    Codec:             {a.codec or '(none)'}")
            p(f"    Sample rate:       {a.sample_rate}")
            p(f"    Channels:          {a.channels}")
            p(f"    SHA-256:           {a.sha256 or '(not computed)'}")
            if a.db_sha256:
                p(f"    DB-recorded SHA-256 matches: {'Yes' if a.sha256_matches_db else 'NO - MISMATCH'}")
            if a.verify_error:
                p(f"    Verification error: {a.verify_error}")
            p("")
    else:
        p("  (no artifacts captured)")
        p("")

    p("Database Verification")
    p("----------------------")
    dbv = report.database_verification
    if dbv.checked:
        p(f"  PRAGMA integrity_check:     {dbv.integrity_check} ({'OK' if dbv.integrity_ok else 'FAILED'})")
        p(f"  PRAGMA foreign_key_check:   {'OK - no violations' if dbv.foreign_keys_ok else dbv.foreign_key_violations}")
        p("  Expected table row counts:")
        for table, count in dbv.table_row_counts.items():
            p(f"    {table}: {count}")
        if dbv.missing_expected_rows:
            p("  Missing expected rows:")
            for m in dbv.missing_expected_rows:
                p(f"    - {m}")
    elif dbv.error:
        p(f"  ERROR: {dbv.error}")
    else:
        p("  (not run - workflow stopped before a disposable database existed)")
    p("")

    p("ffprobe Verification")
    p("---------------------")
    ffprobe_checked = [a for a in report.artifacts if a.verified]
    p(f"  {len(ffprobe_checked)}/{len(report.artifacts)} artifact(s) independently verified via ffprobe.")
    p("")

    p("External Dependency Failures")
    p("-----------------------------")
    ext_failures = [s for s in report.stages if s.failure_ownership == _EXTERNAL_DEPENDENCY_OWNERSHIP]
    if ext_failures:
        for s in ext_failures:
            p(f"  Stage {s.number} ({s.stage}): {s.failure_reason}")
    else:
        p("  (none)")
    p("")

    if is_publishing:
        p("Publishing Readiness")
        p("---------------------")
        p(f"  Verdict: {report.publishing_readiness}")
        p("")
        if report.publishing_findings:
            for f in report.publishing_findings:
                p(f"  [{f.severity}] {f.category}")
                p(f"    Evidence:       {f.evidence}")
                if f.recommendation:
                    p(f"    Recommendation: {f.recommendation}")
                p("")
        else:
            p("  (no findings recorded)")
            p("")

        p("External Dependency Summary (Publishing Providers)")
        p("----------------------------------------------------")
        p("  Do not publish: verified true - no code path in this run ever")
        p("  attempted or could attempt a real external upload.")
        p("")
        for eds in report.external_dependency_status:
            p(f"  [{eds.platform}]")
            p(f"    Credentials configured: {'Yes' if eds.credentials_configured else 'No'} "
              f"(checked: {', '.join(eds.credentials_env_vars_checked)})")
            p(f"    Endpoint reachable:      {eds.endpoint_reachable}")
            p(f"    Real upload code exists: {'Yes' if eds.real_upload_code_exists else 'No'}")
            p(f"    Notes: {eds.notes}")
            p("")

    p("Production Integrity")
    p("--------------------")
    db_ok = report.production_db_before == report.production_db_after
    p(f"  DB unchanged: {'Yes' if db_ok else 'NO - DATABASE WAS MODIFIED'}")
    if report.production_db_size_before:
        p(f"  DB size before: {report.production_db_size_before}")
        p(f"  DB size after:  {report.production_db_size_after}")
    git_ok = report.clonecast_git_status_before == report.clonecast_git_status_after
    p(f"  CloneCast Git unchanged: {'Yes' if git_ok else 'NO - WORKTREE CHANGED'}")
    if not git_ok:
        p(f"  Git before: {report.clonecast_git_status_before or '(clean)'}")
        p(f"  Git after:  {report.clonecast_git_status_after or '(clean)'}")
    p("")

    p("Cleanup Verification")
    p("---------------------")
    if report.cleanup_attempted:
        p(f"  Disposable directory removed: {'Yes' if report.cleanup_removed else 'NO'}")
        if report.cleanup_error:
            p(f"  Cleanup error: {report.cleanup_error}")
    else:
        p("  (not attempted - no disposable directory was created)")
    p("")

    p(f"No Changes Made to Production: {'Yes' if db_ok and git_ok else 'NO'}")
    p("")
    p(f"Final Status: {report.overall_status}")
    return "\n".join(lines) + "\n"


def cmd_workflow_test(args) -> int:
    """Disposable workflow test: runs a real CloneCast workflow against a
    disposable environment. Never modifies production data."""
    if not getattr(args, "disposable", False):
        print("Disposable Workflow Test")
        print("========================")
        print()
        print("Overall Status:   SAFETY_BLOCKED")
        print()
        print("Blockers:")
        print("  - --disposable flag is REQUIRED for workflow testing.")
        print("  - Without it, no temporary files are created, no database is")
        print("    copied, no server is started, and no HTTP requests are sent.")
        print()
        print("No Changes Made: Yes")
        return 1

    repo_root = _resolve_repo(args)
    report = workflow_test.run_workflow_test(repo_root, port=8000)

    text = _render_workflow_report(report)
    print(text)

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase_1x_report.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Report saved to: {report_path}")

    return 0 if report.overall_status == "DISPOSABLE_WORKFLOW_COMPLETE" else 1


def cmd_publish_test(args) -> int:
    """Publishing validation: reuses the Phase 1X disposable workflow to
    produce a real completed episode, then continues through every
    publishing stage CloneCast supports (QC, release readiness, packaging,
    local publication, platform export) up to but never including any real
    external upload. Never modifies production data; never publishes
    anything externally - confirmed both by CloneCast's own DB-level
    constraints (destination_type CHECK'd to 'local' only) and by this
    harness's own upload_status assertions."""
    if not getattr(args, "disposable", False):
        print("Publishing Validation Test")
        print("==========================")
        print()
        print("Overall Status:   SAFETY_BLOCKED")
        print()
        print("Blockers:")
        print("  - --disposable flag is REQUIRED for publishing validation.")
        print("  - Without it, no temporary files are created, no database is")
        print("    copied, no server is started, and no HTTP requests are sent.")
        print()
        print("No Changes Made: Yes")
        return 1

    repo_root = _resolve_repo(args)
    report = workflow_test.run_workflow_test(repo_root, port=8000, include_publishing=True)

    text = _render_workflow_report(report)
    print(text)

    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase_1y_report.txt")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"Report saved to: {report_path}")

    success = (report.overall_status == "DISPOSABLE_WORKFLOW_COMPLETE"
              and report.publishing_readiness in ("PASS", "WARNING"))
    return 0 if success else 1


def cmd_quick_podcast(args) -> int:
    report = quick_podcast.run(args)
    quick_podcast.print_report(report)
    return 0 if report.overall == "PASS" else 1


def _print_chat_response(response: chat.ChatResponse) -> None:
    print(response.text)
    if response.commands:
        print()
        print("Suggested Commands")
        print("------------------")
        for command in response.commands:
            print(f"  {command}")


def cmd_chat(args) -> int:
    """Repository-aware conversational interface over AutoCorp capabilities."""
    repo_root = _resolve_repo(args)
    session = chat.AutoCorpChatSession(repo_root)
    initial = " ".join(getattr(args, "prompt", []) or []).strip()
    if initial:
        _print_chat_response(session.handle(initial))
        return 0

    print("AutoCorp Chat")
    print("=============")
    print()
    print("Ask about scans, health, blockers, workflow tests, repair plans, commits, branches, or prompts.")
    print("Type 'exit' to quit.")
    while True:
        try:
            request = input("autocorp chat> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            print("Interrupted.", file=sys.stderr)
            return 130
        if request.casefold() in {"exit", "quit", "q"}:
            return 0
        if not request:
            continue
        _print_chat_response(session.handle(request))
        print()


def cmd_manage(args) -> int:
    """Autonomous Engineering Manager: read-only coordination over existing
    scanner, analyzer, planner, readiness, git, docs, repair, chat, and
    Reliability Engine evidence."""
    repo_root = _resolve_repo(args)
    report = manager.run_manager(repo_root)
    if getattr(args, "roadmap", False):
        print(manager.render_roadmap(report))
    elif getattr(args, "next_task", False):
        print(manager.render_next_task(report))
    elif getattr(args, "production", False):
        print(manager.render_production(report))
    else:
        print(manager.render_summary(report))
    return 0


def cmd_discover(args) -> int:
    """Universal Repository Discovery: read-only repository profiling with
    AutoCorp metadata storage."""
    repo_root = _resolve_repo(args, quiet=getattr(args, "json", False))
    profile = discovery.discover_repository(repo_root, store_profile=True)
    if getattr(args, "json", False):
        print(discovery.profile_to_json(profile))
    else:
        print(discovery.render_profile(profile, full=getattr(args, "full", False)))
    return 0


def cmd_inspect(args) -> int:
    """Live Application Inspector: launch detected applications from a
    disposable copy and report runtime evidence."""
    repo_root = _resolve_repo(args, quiet=getattr(args, "json", False))
    report = live_inspector.inspect_application(
        repo_root,
        timeout=getattr(args, "timeout", 10),
        preferred_port=getattr(args, "port", 0),
    )
    if getattr(args, "json", False):
        print(live_inspector.inspection_to_json(report))
    else:
        print(live_inspector.render_inspection(report, full=getattr(args, "full", False)))
    return 0 if report.running_application in {"PASS", "STARTED", "PARTIAL"} else 1


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

    sp = sub.add_parser("live-readiness",
                        help="static live-application readiness scanner")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.set_defaults(func=cmd_live_readiness)

    sp = sub.add_parser("live-test",
                        help="controlled live application test (safe startup, "
                             "health checks, service readiness)")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--port", default=8000, type=int, metavar="PORT",
                    help="expected application port (default: 8000)")
    sp.add_argument("--timeout", default=30, type=int, metavar="SEC",
                    help="startup timeout in seconds (default: 30)")
    sp.set_defaults(func=cmd_live_test)

    sp = sub.add_parser("workflow-test",
                        help="disposable episode workflow test (safe, isolated)")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--disposable", action="store_true",
                    help="REQUIRED: enable disposable isolation (refuses without it)")
    sp.set_defaults(func=cmd_workflow_test)

    sp = sub.add_parser("publish-test",
                        help="disposable publishing validation test (safe, isolated, "
                             "never uploads externally)")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--disposable", action="store_true",
                    help="REQUIRED: enable disposable isolation (refuses without it)")
    sp.set_defaults(func=cmd_publish_test)

    sp = sub.add_parser("chat",
                        help="repository-aware AutoCorp Chat")
    sp.add_argument("prompt", nargs="*",
                    help="optional one-shot chat request")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("manage",
                        help="Autonomous Engineering Manager")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true",
                      help="show engineering summary (default)")
    mode.add_argument("--roadmap", action="store_true",
                      help="show live evidence-backed roadmap")
    mode.add_argument("--next-task", action="store_true",
                      help="show the highest-priority recommended task")
    mode.add_argument("--production", action="store_true",
                      help="show production/release readiness scoring")
    sp.set_defaults(func=cmd_manage)

    sp = sub.add_parser("discover",
                        help="universal repository discovery profile")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON profile")
    sp.add_argument("--full", action="store_true",
                    help="include evidence lines for each finding")
    sp.set_defaults(func=cmd_discover)

    sp = sub.add_parser("inspect",
                        help="live application inspector")
    sp.add_argument("--repo", default=None, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON inspection report")
    sp.add_argument("--full", action="store_true",
                    help="include captured stdout/stderr")
    sp.add_argument("--port", default=0, type=int, metavar="PORT",
                    help="preferred local port, or 0 for an ephemeral port")
    sp.add_argument("--timeout", default=10, type=int, metavar="SEC",
                    help="startup timeout in seconds (default: 10)")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("quick-podcast",
                        help="generate a real disposable CloneCast podcast for listening")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to CloneCast repository")
    sp.add_argument("--topic", required=True,
                    help="podcast topic")
    sp.add_argument("--show", default="CloneCast",
                    help="show name (default: CloneCast)")
    sp.add_argument("--duration", default="10m",
                    help="target duration such as 10m or 600s (default: 10m)")
    sp.add_argument("--voice", default="",
                    help="approved CloneCast voice_profile_id to use")
    sp.add_argument("--output", default="",
                    help="output directory (default: /tmp/autocorp_quick_podcast_output/<repo>/test_episode)")
    sp.add_argument("--test", action="store_true",
                    help="required safety mode: never publish externally")
    sp.set_defaults(func=cmd_quick_podcast)

    sp = sub.add_parser("episode-build",
                        help="guided CloneCast episode operator with review lock")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to CloneCast repository")
    sp.add_argument("--resume", default=None, metavar="SESSION_ID",
                    help="resume a saved guided episode session")
    sp.add_argument("--approve", default=None, metavar="SESSION_ID",
                    help="record owner approval after listening; does not publish")
    sp.add_argument("--reject", default=None, metavar="SESSION_ID",
                    help="record owner rejection and keep publishing locked")
    sp.add_argument("--owner", default="owner",
                    help="approval actor recorded in the AutoCorp session")
    sp.add_argument("--reason", default="",
                    help="review rejection reason")
    sp.add_argument("--regenerate-section", default=None, metavar="SECTION_ID",
                    help="record regeneration of one failed or rejected section")
    sp.set_defaults(func=cmd_episode_build)

    sp = sub.add_parser("test-plan",
                        help="Fast Pytest Engine: read-only test plan (runs nothing)")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--feature", default=None,
                    help="feature/subsystem name to include FOCUSED candidates for")
    sp.add_argument("--base-branch", default=None, metavar="BRANCH",
                    help="compare committed changes against this branch")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_test_plan)

    sp = sub.add_parser("test-fast",
                        help="Fast Pytest Engine: immediate developer feedback "
                             "(never grants production approval)")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_test_fast)

    sp = sub.add_parser("test-focused",
                        help="Fast Pytest Engine: verify one feature/subsystem "
                             "(never grants production approval)")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--feature", default=None, help="feature/subsystem name to verify")
    sp.add_argument("--path", action="append", default=None, metavar="TEST_PATH",
                    help="explicit test file path, e.g. tests/test_example.py")
    sp.add_argument("--node-id", default=None, metavar="NODE_ID",
                    help="explicit pytest node ID, e.g. tests/test_example.py::TestClass::test_case")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_test_focused)

    sp = sub.add_parser("test-full",
                        help="Fast Pytest Engine: the repository's complete strict "
                             "verification, run unmodified (only mode that can "
                             "contribute to production approval)")
    sp.add_argument("--repo", required=True, metavar="PATH",
                    help="absolute path to target repository")
    sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sp.set_defaults(func=cmd_test_full)

    return p


def main() -> int:
    config.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    try:
        if not args.command:
            return repl(args.auto, args.watchdog)
        return args.func(args)
    except KeyboardInterrupt:
        print()
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

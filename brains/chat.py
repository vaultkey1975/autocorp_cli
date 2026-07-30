#!/usr/bin/env python3
"""Repository-aware AutoCorp Chat.

This is intentionally not a generic LLM wrapper. It is a small conversational
router over AutoCorp's existing repository-intelligence, repair, workflow, and
documentation capabilities. Every answer is derived from the repository at call
time or from explicit command guidance for an existing AutoCorp command.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

from brains import analyzer, discovery, manager, project_planner, scanner


@dataclass(frozen=True)
class ChatResponse:
    intent: str
    text: str
    commands: tuple[str, ...] = ()
    blocked: bool = False


@dataclass
class AutoCorpChatSession:
    repo_root: str
    autocorp_root: str | None = None
    history: list[tuple[str, ChatResponse]] = field(default_factory=list)
    last_scan: scanner.ScanResult | None = None
    last_analysis: analyzer.ProjectAnalysis | None = None
    last_plan: project_planner.ProjectPlan | None = None

    def __post_init__(self) -> None:
        self.repo_root = os.path.abspath(self.repo_root)
        if self.autocorp_root is None:
            self.autocorp_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def handle(self, message: str) -> ChatResponse:
        message = (message or "").strip()
        if not message:
            response = ChatResponse("empty", "Ask about repository health, blockers, workflow tests, repair plans, commits, branches, or prompt preparation.")
            self.history.append((message, response))
            return response
        lower = message.casefold()

        if _matches(lower, "show repository profile", "repository profile"):
            response = self._discovery_profile()
        elif _matches(lower, "show architecture", "architecture"):
            response = self._discovery_section("architecture")
        elif _matches(lower, "show frameworks", "frameworks"):
            response = self._discovery_section("frameworks")
        elif _matches(lower, "show languages", "languages"):
            response = self._discovery_section("languages")
        elif _matches(lower, "show build system", "build system"):
            response = self._discovery_section("build")
        elif _matches(lower, "show testing", "testing"):
            response = self._discovery_section("testing")
        elif _matches(lower, "show deployment", "deployment"):
            response = self._discovery_section("deployment")
        elif _matches(lower, "show engineering maturity", "engineering maturity"):
            response = self._discovery_section("maturity")
        elif _matches(lower, "show production readiness", "production readiness", "show release status", "release status"):
            response = self._manager_production()
        elif _matches(lower, "show roadmap", "roadmap"):
            response = self._manager_roadmap()
        elif _matches(lower, "show next task", "next task", "what should i work on next"):
            response = self._manager_next_task()
        elif _matches(lower, "show engineering summary", "engineering summary", "manager summary"):
            response = self._manager_summary()
        elif _matches(lower, "show blockers", "blockers"):
            response = self._manager_roadmap()
        elif _matches(lower, "scan", "repository scan", "scan my repository"):
            response = self._scan()
        elif _matches(lower, "health", "repository health", "show repository health", "what is broken", "broken"):
            response = self._health()
        elif _matches(lower, "next", "what phases remain"):
            response = self._next_steps(lower)
        elif _matches(lower, "summarize today's work", "summarize todays work", "today's work", "todays work"):
            response = self._todays_work()
        elif lower.startswith("explain this error") or lower.startswith("explain error") or "why did this verification fail" in lower:
            response = self._explain_error(message)
        elif "run a disposable workflow" in lower or "workflow-test" in lower:
            response = self._workflow_command()
        elif "publish-test" in lower or "publishing validation" in lower:
            response = self._publish_command()
        elif "generate a repair plan" in lower or "repair plan" in lower:
            response = self._repair_plan()
        elif "review this commit" in lower or lower.startswith("review commit"):
            response = self._review_commit(message)
        elif "compare branches" in lower or lower.startswith("compare "):
            response = self._compare_branches(message)
        elif "prepare a codex prompt" in lower or "prepare a claude prompt" in lower or "prepare a deepseek prompt" in lower:
            response = self._prepare_prompt(message)
        elif "continue previous engineering session" in lower or "continue session" in lower:
            response = self._continue_session()
        elif "reliability" in lower:
            response = self._reliability_status()
        elif lower in {"help", "?", "commands"}:
            response = self._help()
        else:
            response = self._help(prefix="I do not have a repository-backed route for that wording yet.")

        self.history.append((message, response))
        return response

    def _scan(self) -> ChatResponse:
        self.last_scan = scanner.run_scan(self.repo_root)
        result = self.last_scan
        return ChatResponse(
            "scan",
            "\n".join([
                "Repository Scan",
                f"Repository: {result.repo_path}",
                f"Branch: {result.branch}",
                f"Working Tree: {result.working_tree}",
                f"Python Files: {result.python_file_count}",
                f"Test Files: {result.test_file_count}",
                f"TODO: {result.todo_count}",
                f"FIXME: {result.fixme_count}",
                f"pass Statements: {result.pass_count}",
                f"NotImplementedError: {result.not_implemented_count}",
            ]),
        )

    def _health(self) -> ChatResponse:
        self.last_scan = scanner.run_scan(self.repo_root)
        self.last_analysis = analyzer.run_analysis(self.repo_root)
        self.last_plan = project_planner.run_project_plan(self.repo_root)
        plan = self.last_plan
        action_lines = [f"- [{a.priority}] {a.title}: {a.reason}" for a in plan.actions[:5]]
        blocker_lines = [f"- {blocker}" for blocker in plan.blockers] or ["- (none)"]
        return ChatResponse(
            "health",
            "\n".join([
                "Repository Health",
                f"Repository: {plan.repo_path}",
                f"Project Type: {plan.project_type}",
                f"Overall Health: {plan.overall_health}",
                f"Working Tree: {self.last_scan.working_tree}",
                "",
                "Blockers:",
                *blocker_lines,
                "",
                "Top Actions:",
                *(action_lines or ["- (none)"]),
            ]),
        )

    def _next_steps(self, lower: str) -> ChatResponse:
        docs = []
        for rel in ("AI_ENGINEERING/CURRENT_PHASE.md", "AI_ENGINEERING/NEXT_STEPS.md", "AI_ENGINEERING/ROADMAP.md"):
            path = os.path.join(self.autocorp_root or self.repo_root, rel)
            if os.path.isfile(path):
                docs.append(f"--- {rel} ---\n{_read_limited(path, 2200)}")
        intent = "roadmap" if "roadmap" in lower or "phase" in lower else "next_steps"
        return ChatResponse(intent, "\n\n".join(docs) if docs else "Unable to determine from repository evidence.")

    def _todays_work(self) -> ChatResponse:
        today = _dt.date.today().isoformat()
        proc = _run_git(self.repo_root, ["log", "--since", f"{today} 00:00", "--oneline", "--decorate", "--all", "--max-count=30"])
        lines = proc.stdout.strip().splitlines()
        status = _run_git(self.repo_root, ["status", "--short", "--branch"]).stdout.strip()
        body = ["Today's Work", f"Date: {today}", "", "Commits:"]
        body.extend(lines or ["(none found today)"])
        body.extend(["", "Current Status:", status or "(clean)"])
        return ChatResponse("todays_work", "\n".join(body))

    def _explain_error(self, message: str) -> ChatResponse:
        text = message.split(":", 1)[1].strip() if ":" in message else message
        hints = []
        if "unboundlocalerror" in text.casefold():
            hints.append("A local variable is read before assignment. Check early-return/error paths around the named variable.")
        if "no module named" in text.casefold() or "modulenotfounderror" in text.casefold():
            hints.append("A dependency or import path is missing. Check requirements and PYTHONPATH/working directory.")
        if "permission denied" in text.casefold():
            hints.append("A filesystem or executable permission check failed.")
        if "pytest" in text.casefold() or "assert" in text.casefold():
            hints.append("Use the failing test name and assertion as the repair target; rerun the focused test before the full suite.")
        if not hints:
            hints.append("No specialized classifier matched. Use `scan`, `show repository health`, or `generate a repair plan` for repository-backed next steps.")
        return ChatResponse("explain_error", "\n".join(["Error Explanation", f"Input: {text}", "", *hints]))

    def _workflow_command(self) -> ChatResponse:
        repo = shlex.quote(self.repo_root)
        python = shlex.quote(sys.executable)
        return ChatResponse(
            "workflow_test",
            "Disposable workflow testing is exposed through the existing workflow-test command. It refuses to run without --disposable.",
            commands=(f"{python} autocorp.py workflow-test --repo {repo} --disposable",),
            blocked=True,
        )

    def _publish_command(self) -> ChatResponse:
        repo = shlex.quote(self.repo_root)
        python = shlex.quote(sys.executable)
        return ChatResponse(
            "publish_test",
            "Disposable publishing validation is exposed through publish-test. It stops at the verified external-upload boundary.",
            commands=(f"{python} autocorp.py publish-test --repo {repo} --disposable",),
            blocked=True,
        )

    def _repair_plan(self) -> ChatResponse:
        self.last_plan = project_planner.run_project_plan(self.repo_root)
        if not self.last_plan.actions:
            return ChatResponse("repair_plan", "No repository-supported repair actions were produced by plan-project.")
        action = self.last_plan.actions[0]
        repo = shlex.quote(self.repo_root)
        python = shlex.quote(sys.executable)
        return ChatResponse(
            "repair_plan",
            "\n".join([
                "Repair Plan",
                f"Action: {action.title}",
                f"Action ID: {action.action_id}",
                f"Reason: {action.reason}",
                f"Safe to automate: {'Yes' if action.safe_to_automate else 'No'}",
            ]),
            commands=(f"{python} autocorp.py repair --repo {repo} --action {shlex.quote(action.action_id)} --dry-run",),
        )

    def _review_commit(self, message: str) -> ChatResponse:
        ref = _first_ref(message) or "HEAD"
        proc = _run_git(self.repo_root, ["show", "--stat", "--oneline", "--decorate", "--no-renames", ref])
        if proc.returncode != 0:
            return ChatResponse("review_commit", proc.stderr.strip() or f"Unable to read commit {ref}.", blocked=True)
        return ChatResponse("review_commit", proc.stdout.strip())

    def _compare_branches(self, message: str) -> ChatResponse:
        refs = _refs(message)
        left, right = (refs + ["origin/main", "HEAD"])[:2]
        proc = _run_git(self.repo_root, ["diff", "--stat", f"{left}...{right}"])
        if proc.returncode != 0:
            return ChatResponse("compare_branches", proc.stderr.strip() or f"Unable to compare {left} and {right}.", blocked=True)
        return ChatResponse("compare_branches", f"Branch comparison: {left}...{right}\n{proc.stdout.strip() or '(no diff)'}")

    def _prepare_prompt(self, message: str) -> ChatResponse:
        model = "Codex"
        lower = message.casefold()
        if "claude" in lower:
            model = "Claude"
        elif "deepseek" in lower:
            model = "DeepSeek"
        status = _run_git(self.repo_root, ["status", "--short", "--branch"]).stdout.strip()
        prompt = "\n".join([
            f"{model} Engineering Prompt",
            "",
            f"Repository: {self.repo_root}",
            "",
            "Instructions:",
            "- Read AI_ENGINEERING/BOOTSTRAP_PROMPT.md and every file in AI_ENGINEERING/.",
            "- Use the repository as the source of truth.",
            "- Run git status and baseline tests before changing code.",
            "- Preserve unrelated dirty work.",
            "- Report exact verification commands and exit codes.",
            "",
            "Current git status:",
            status or "(clean)",
        ])
        return ChatResponse("prepare_prompt", prompt)

    def _continue_session(self) -> ChatResponse:
        status = _run_git(self.repo_root, ["status", "--short", "--branch"]).stdout.strip()
        docs = self._next_steps("next").text
        return ChatResponse("continue_session", "\n".join(["Continue Session", status or "(clean)", "", docs]))

    def _reliability_status(self) -> ChatResponse:
        present = os.path.isdir(os.path.join(self.autocorp_root or self.repo_root, "reliability_engine"))
        tests = os.path.isfile(os.path.join(self.autocorp_root or self.repo_root, "tests", "test_reliability_engine.py"))
        return ChatResponse(
            "reliability_status",
            "\n".join([
                "Reliability Engine",
                f"Source present: {'Yes' if present else 'No'}",
                f"Tests present: {'Yes' if tests else 'No'}",
                "Production status is determined by AI_ENGINEERING/CURRENT_PHASE.md and verification results.",
            ]),
        )

    def _manager_report(self):
        return manager.run_manager(self.repo_root, autocorp_root=self.autocorp_root)

    def _manager_summary(self) -> ChatResponse:
        return ChatResponse("manager_summary", manager.render_summary(self._manager_report()))

    def _manager_roadmap(self) -> ChatResponse:
        return ChatResponse("manager_roadmap", manager.render_roadmap(self._manager_report()))

    def _manager_next_task(self) -> ChatResponse:
        return ChatResponse("manager_next_task", manager.render_next_task(self._manager_report()))

    def _manager_production(self) -> ChatResponse:
        report = self._manager_report()
        return ChatResponse("manager_production", manager.render_production(report), commands=report.production_commands)

    def _profile(self):
        return discovery.discover_repository(self.repo_root, store_profile=True)

    def _discovery_profile(self) -> ChatResponse:
        return ChatResponse("repository_profile", discovery.render_profile(self._profile(), full=False))

    def _discovery_section(self, section: str) -> ChatResponse:
        profile = self._profile()
        if section == "architecture":
            text = _profile_single("Architecture", profile.architecture)
        elif section == "frameworks":
            text = _profile_findings("Frameworks", profile.frameworks)
        elif section == "languages":
            text = _profile_findings("Languages", profile.languages)
        elif section == "build":
            text = _profile_findings("Build System", profile.build_system)
        elif section == "testing":
            text = _profile_findings("Testing", profile.test_frameworks)
        elif section == "deployment":
            text = _profile_findings("Deployment", profile.deployment)
        elif section == "maturity":
            text = _profile_single("Engineering Maturity", profile.engineering_maturity)
        else:
            text = "Unable to determine from repository evidence."
        return ChatResponse(f"repository_{section}", text)

    def _help(self, prefix: str = "") -> ChatResponse:
        lines = []
        if prefix:
            lines.extend([prefix, ""])
        lines.extend([
            "AutoCorp Chat can answer:",
            "- scan my repository",
            "- show repository health / what is broken",
            "- what should I work on next / show blockers / show roadmap",
            "- show production readiness / show release status",
            "- show engineering summary / show next task",
            "- show repository profile / architecture / frameworks / languages",
            "- show build system / testing / deployment / engineering maturity",
            "- summarize today's work",
            "- explain this error: <text>",
            "- run a disposable workflow",
            "- run publishing validation",
            "- generate a repair plan",
            "- review commit <ref>",
            "- compare branches <left> <right>",
            "- prepare a Codex/Claude/DeepSeek prompt",
            "- continue previous engineering session",
        ])
        return ChatResponse("help", "\n".join(lines))


def _matches(lower: str, *phrases: str) -> bool:
    return any(phrase in lower for phrase in phrases)


def _run_git(repo_root: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True)


def _read_limited(path: str, limit: int) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()[:limit].rstrip()


def _profile_single(title: str, finding) -> str:
    lines = [title, "=" * len(title), finding.value]
    if finding.evidence:
        lines.extend(["", "Evidence:"])
        lines.extend(f"- {ev}" for ev in finding.evidence)
    lines.append(f"Confidence: {finding.confidence}%")
    return "\n".join(lines)


def _profile_findings(title: str, findings) -> str:
    lines = [title, "=" * len(title)]
    if not findings:
        lines.append("Unknown - not enough evidence")
        return "\n".join(lines)
    for finding in findings:
        lines.append(f"- {finding.value} ({finding.confidence}%)")
        for ev in finding.evidence:
            lines.append(f"  Evidence: {ev}")
    return "\n".join(lines)


def _first_ref(message: str) -> str:
    refs = _refs(message)
    return refs[0] if refs else ""


def _refs(message: str) -> list[str]:
    ignored = {"review", "this", "commit", "compare", "branches", "branch"}
    refs = []
    for token in re.findall(r"[A-Za-z0-9_./@{}~^-]+", message):
        lowered = token.casefold()
        if lowered in ignored:
            continue
        if len(token) >= 4 and ("/" in token or re.search(r"[0-9a-f]{4,}", token, re.IGNORECASE) or token in {"HEAD", "main", "master"}):
            refs.append(token)
    return refs

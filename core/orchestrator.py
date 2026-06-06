#!/usr/bin/env python3
"""
Orchestrator  (AutoCorp CLI - core)
===================================

The Session ties the brains and memory together into the full loop:

    recall lessons -> plan -> confirm -> build -> test -> fix-loop -> learn

Everything that writes a file or runs a command goes through the Executor, which
consults the gate. To put Agent Watchdog in charge later, construct the Session
with a WatchdogGate instead of a ConfirmGate - nothing here changes.
"""

import os

from config import MAX_FIX_ATTEMPTS, WORKSPACE_DIR
from core import console, llm
from brains.planner import PlannerBrain
from brains.builder import BuilderBrain
from brains.tester import TesterBrain
from memory import store
from safety.executor import Executor


EXPLAIN_SYSTEM_PROMPT = """You are the Explainer of a local AI coding assistant.
Explain the given source file to a developer: what it does, how it works, and any
risks or gotchas. Be clear and concise. Plain text, no JSON."""


class Session:
    def __init__(self, gate, assume_yes: bool = False):
        self.gate = gate
        # When assume_yes is set (e.g. --auto), skip the plan-level confirmation.
        # Per-action safety still flows through the gate.
        self.assume_yes = assume_yes
        self.executor = Executor(gate)
        self.planner = PlannerBrain()
        self.builder = BuilderBrain(self.executor)
        self.tester = TesterBrain(self.executor)
        store.init_db()

    # ------------------------------------------------------------------ #
    # Full pipeline
    # ------------------------------------------------------------------ #
    def run(self, request: str) -> dict:
        request = (request or "").strip()
        if not request:
            console.warn("Empty request; nothing to do.")
            return {"status": "empty"}

        # 1) Recall relevant past knowledge.
        lessons = store.recall_lessons(request)
        lessons_text = store.format_lessons_for_prompt(lessons)
        if lessons:
            console.muted(f"Recalled {len(lessons)} relevant lesson(s) from memory.")

        # 2) Plan.
        console.rule("Plan")
        try:
            plan = self.planner.plan(request, lessons_text)
        except llm.OllamaError as e:
            console.error(f"Planning failed: {e}")
            return {"status": "error", "stage": "plan", "error": str(e)}
        console.show_plan(plan)

        if not self.assume_yes and not console.confirm("Proceed with this plan?", default=True):
            console.warn("Plan declined. Stopping.")
            return {"status": "declined", "plan": plan}

        # 3) Build.
        console.rule("Build")
        workspace = self._make_workspace(plan["project_name"])
        console.info(f"Workspace: [cyan]{workspace}[/cyan]")
        write_results = self.builder.build(plan, workspace, lessons_text)
        written = [w for w in write_results if getattr(w, "written", False)]
        if not written:
            console.error("No files were written.")
            store.record_build(request, plan["project_name"], workspace, plan,
                               status="failed", summary="no files written")
            return {"status": "failed", "stage": "build", "workspace": workspace}

        # 4) Test + fix loop.
        console.rule("Test")
        result = self.tester.test(workspace, plan)
        attempts = 0
        while (not result.ok) and (not result.blocked) and attempts < MAX_FIX_ATTEMPTS:
            attempts += 1
            console.rule(f"Fix attempt {attempts}/{MAX_FIX_ATTEMPTS}")
            target = self.tester.pick_file_to_fix(plan, result.output)
            if not target:
                break
            fix = self.tester.suggest_fix(workspace, target, result.output, plan)
            if not fix:
                console.warn("No fix suggestion available; stopping fix loop.")
                break
            console.info(f"Fix: {fix.get('explanation','(no explanation)')}")
            console.show_code(target, fix["new_content"],
                              lexer="python" if target.endswith(".py") else "text")
            wr = self.executor.write_file(os.path.join(workspace, target), fix["new_content"])
            if not wr.written:
                break
            # Record the mistake -> fix as a reusable lesson.
            store.record_lesson(
                kind="fix",
                title=f"Fixed {target} in {plan['project_name']}",
                problem=result.output[:500],
                solution=fix.get("explanation", ""),
                tags=f"{plan['language']} {plan['project_name']} {os.path.basename(target)}",
            )
            result = self.tester.test(workspace, plan)

        # 5) Record outcome.
        status = "passed" if result.ok else ("blocked" if result.blocked else "failed")
        summary = f"{plan['summary']} — tests {status} after {attempts} fix attempt(s)."
        store.record_build(request, plan["project_name"], workspace, plan,
                           status=status, summary=summary)
        if result.ok:
            store.record_lesson(
                kind="success",
                title=f"Built {plan['project_name']}",
                problem=request,
                solution=f"Files: {', '.join(f['path'] for f in plan['files'])}. "
                         f"Test: {plan.get('test_command','')}",
                tags=f"{plan['language']} {plan['project_name']}",
            )

        self._final_report(plan, workspace, status, attempts)
        return {"status": status, "plan": plan, "workspace": workspace,
                "fix_attempts": attempts}

    # ------------------------------------------------------------------ #
    # Explain
    # ------------------------------------------------------------------ #
    def explain(self, path: str) -> str:
        if not os.path.isfile(path):
            console.error(f"No such file: {path}")
            return ""
        with open(path, encoding="utf-8") as f:
            content = f.read()
        console.info(f"Explaining [cyan]{path}[/cyan] ...")
        try:
            text = llm.generate(
                f"FILE: {path}\n\n{content}\n\nExplain this file.",
                system=EXPLAIN_SYSTEM_PROMPT,
            )
        except llm.OllamaError as e:
            console.error(f"Explain failed: {e}")
            return ""
        console.show_panel(f"Explanation — {os.path.basename(path)}", text.strip(), "cyan")
        return text

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _make_workspace(self, project_name: str) -> str:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        path = os.path.join(WORKSPACE_DIR, project_name)
        # If the name is taken, suffix it so we never clobber a prior build.
        if os.path.exists(path):
            n = 2
            while os.path.exists(f"{path}_{n}"):
                n += 1
            path = f"{path}_{n}"
        os.makedirs(path, exist_ok=True)
        return path

    def _final_report(self, plan: dict, workspace: str, status: str, attempts: int) -> None:
        color = {"passed": "green", "failed": "red", "blocked": "yellow"}.get(status, "cyan")
        console.rule("Result")
        console.show_panel(
            f"{plan['project_name']} — {status.upper()}",
            f"{plan['summary']}\n\n"
            f"Location : {workspace}\n"
            f"Test     : {plan.get('test_command','')}\n"
            f"Fixes    : {attempts}",
            style=color,
        )

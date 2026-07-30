#!/usr/bin/env python3
"""Top-level Reliability Engine loop.

This layer calls the existing AutoCorp brains as services. It does not replace
their implementation and it does not introduce any new human approval gate.
"""

import os
import shlex
import sys
from typing import Any, cast

from brains.builder import EDIT_DIFF_SYSTEM_PROMPT, BuilderBrain
from brains.local_engine import LocalEngine
from brains.planner import PlannerBrain
from brains.tester import TesterBrain
from config import WORKSPACE_DIR
from core import console
from memory import store
from reliability_engine import state_store
from reliability_engine.config_loader import load_config
from reliability_engine.dep_graph import DependencyGraph
from reliability_engine.edit_router import classify_request, read_targets
from reliability_engine.model_availability import ReliabilityModelRouter
from reliability_engine.planner_spec import persist_subtasks, validate_or_repair
from reliability_engine.rag_index import CodebaseRAGIndex
from reliability_engine.regression_runner import RegressionRunner
from reliability_engine.self_consistency import GeneratedCandidate, SelfConsistencyRunner
from reliability_engine.test_loop import ReliabilityTestLoop, build_patch_prompt, test_coverage_expected
from reliability_engine.worktree_sandbox import WorktreeSandbox
from safety.executor import Executor


class ReliabilityOrchestrator:
    def __init__(self, gate, repo_root: str | None = None, config_path: str = "reliability_config.yaml",
                 assume_yes: bool = False):
        self.repo_root = os.path.abspath(repo_root or os.getcwd())
        self.config = load_config(os.path.join(self.repo_root, config_path))
        self.gate = gate
        self.assume_yes = assume_yes
        self.executor = Executor(gate)
        self.planner = PlannerBrain()
        self.tester = TesterBrain(self.executor)
        self.regression = RegressionRunner()
        self.sandbox = WorktreeSandbox(
            self.repo_root,
            base_branch=self.config["worktree_sandbox"]["base_branch"],
        )
        store.init_db()

    def _workspace_for_state(self, plan: dict) -> str:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        path = os.path.join(WORKSPACE_DIR, f"{plan.get('project_name', 'reliability')}_state")
        os.makedirs(path, exist_ok=True)
        return path

    def _edit_plan(self, request: str, targets: list[str]) -> dict:
        return {
            "project_name": "repo_edit",
            "project_type": "edit",
            "language": "python",
            "summary": request,
            "files": [{"path": path, "purpose": request} for path in targets],
            "build_order": list(targets),
            "test_command": f"{shlex.quote(sys.executable)} -m pytest -q",
            "success_criteria": ["requested edit is implemented", "tests pass"],
            "edit_mode": True,
        }

    def _exclude_edit_reference(self, relpath: str) -> bool:
        normalized = relpath.replace(os.sep, "/")
        prompt_files = {
            "brains/builder.py",
            "brains/planner.py",
            "brains/tester.py",
            "brains/repair_proposal.py",
        }
        return normalized.startswith("reliability_engine/") or normalized in prompt_files

    def _dependency_context(self, dep_graph: DependencyGraph, files: list[str], limit: int = 5) -> list[dict]:
        related: list[str] = []
        for path in files:
            normalized = path.replace(os.sep, "/")
            for candidate in sorted(dep_graph.imports.get(normalized, set()) | dep_graph.dependents(normalized)):
                display = candidate.replace(os.sep, "/")
                if self._exclude_edit_reference(display):
                    continue
                if display not in files and display not in related:
                    related.append(display)
        out = []
        for rel in related[:limit]:
            path = os.path.join(self.repo_root, rel)
            try:
                with open(path, encoding="utf-8") as fh:
                    document = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            out.append({
                "document": document[:3000],
                "metadata": {
                    "path": rel,
                    "start_line": 1,
                    "source": "dependency_graph",
                    "role": "inert reference code",
                },
            })
        return out

    def _tracked_file_exists(self, relpath: str) -> bool:
        import subprocess

        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relpath],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
        )
        return proc.returncode == 0

    def _test_targets_for(self, files: list[str]) -> list[str]:
        tests = []
        for path in files:
            normalized = path.replace(os.sep, "/")
            if normalized.startswith("tests/") or not normalized.endswith(".py"):
                continue
            module = normalized[:-3].replace("/", "_")
            candidates = [
                f"tests/test_{module}.py",
                f"tests/{module}_test.py",
            ]
            stem = normalized.rsplit("/", 1)[-1][:-3]
            candidates.extend([f"tests/test_{stem}.py", f"tests/{stem}_test.py"])
            for candidate in candidates:
                if candidate not in tests and self._tracked_file_exists(candidate):
                    tests.append(candidate)
                    break
        return tests

    def run(self, request: str) -> dict:
        lessons = store.format_lessons_for_prompt(store.recall_lessons(request))
        router_cfg = self.config["model_router"]
        decision = ReliabilityModelRouter(
            router_cfg["builder_model"],
            router_cfg["fallback_model"],
        ).route()
        builder_engine = LocalEngine(model=decision.model)

        dep_graph = DependencyGraph(self.repo_root).build()
        route = classify_request(self.repo_root, request)
        rag = None
        edit_markers = ("add ", "change ", "update ", "modify ", "fix ")
        if not route.is_edit and any(word in request.lower() for word in edit_markers):
            rag = CodebaseRAGIndex(self.repo_root)
            rag.rebuild()
            inferred = rag.query(request, n_results=1)
            if inferred:
                meta = inferred[0].get("metadata") or {}
                path = meta.get("path")
                if path:
                    route = classify_request(self.repo_root, request, inferred_target=str(path))
        if rag is None and not route.is_edit:
            rag = CodebaseRAGIndex(self.repo_root)
            rag.rebuild()

        if route.is_edit:
            edit_targets = list(route.targets)
            if test_coverage_expected(request):
                for test_target in self._test_targets_for(edit_targets):
                    if test_target not in edit_targets:
                        edit_targets.append(test_target)
            plan = self._edit_plan(request, edit_targets)
            console.show_plan(plan)
            if not self.assume_yes and not console.confirm("Proceed with this plan?", default=True):
                return {"status": "declined", "plan": plan}
            state_workspace = self._workspace_for_state(plan)
            subtasks: list[dict[str, Any]] = []
            state_store.reset_subtasks()
            subtask_id = state_store.create_subtask(
                request,
                edit_targets,
                0,
                "pending",
                inferred_target=route.inferred_target,
            )
            subtasks.append({
                "id": subtask_id,
                "description": request,
                "status": "pending",
                "files_touched": edit_targets,
                "blast_radius": 0,
                "attempts": 0,
                "inferred_target": route.inferred_target,
                "edit_mode": True,
            })
            state_store.write_project_state(state_workspace, subtasks)
        else:
            plan = self.planner.plan(request, lessons)
            console.show_plan(plan)
            if not self.assume_yes and not console.confirm("Proceed with this plan?", default=True):
                return {"status": "declined", "plan": plan}
            state_workspace = self._workspace_for_state(plan)
            subtasks = cast(
                list[dict[str, Any]],
                persist_subtasks(validate_or_repair(plan).subtasks, state_workspace),
            )

        results = []
        for subtask in subtasks:
            subtask_id = int(cast(int | str, subtask["id"]))
            description = str(subtask.get("description", ""))
            touched = cast(list[object], subtask.get("files_touched") or [])
            files = [str(path) for path in touched]
            blast_radius = dep_graph.blast_radius(files)
            subtask["blast_radius"] = blast_radius
            state_store.update_subtask(subtask_id, status="in_progress", blast_radius=blast_radius)
            state_store.write_project_state(state_workspace)

            worktree = self.sandbox.create(subtask_id)
            try:
                if subtask.get("edit_mode") and not route.inferred_target:
                    context = []
                else:
                    context = rag.query(description, n_results=5) if rag is not None else []
                builder = BuilderBrain(Executor(self.gate), engine=builder_engine)
                patch_generator = None
                if subtask.get("edit_mode"):
                    target_contents = read_targets(worktree.path, files)
                    def patch_generator(st, related_context, error_trace, _builder=builder):
                        target = str(st["files_touched"][0])
                        return _builder.generate_edit_diff(
                            request,
                            target,
                            target_contents[target],
                            related_context,
                            error_trace,
                            target_contents,
                        )
                else:
                    # Existing builder is still invoked as a service so deterministic
                    # plan-provided files can be materialized when present.
                    planned_files = cast(list[dict[str, Any]], plan.get("files", []))
                    builder.build(
                        {"files": [f for f in planned_files if f.get("path") in files],
                         "build_order": files,
                         "project_name": plan.get("project_name"),
                         "language": plan.get("language"),
                         "summary": plan.get("summary")},
                        worktree.path,
                        lessons,
                    )
                consistency_cfg = self.config["self_consistency"]
                consistency = SelfConsistencyRunner(
                    self.tester,
                    builder_engine,
                    n_samples=consistency_cfg["n_samples"],
                    trigger_blast_radius=consistency_cfg["trigger_blast_radius"],
                )
                if consistency.should_run(subtask, dep_graph.touches_core(files)):
                    if subtask.get("edit_mode"):
                        def candidate_generator(index, error_trace, _builder=builder):
                            target = str(subtask["files_touched"][0])
                            candidate_error = f"candidate sample {index}\n{error_trace}".strip()
                            prompt = _builder.edit_diff_prompt(
                                request,
                                target,
                                target_contents[target],
                                context,
                                candidate_error,
                                target_contents,
                            )
                            raw = builder_engine.generate(prompt, system=EDIT_DIFF_SYSTEM_PROMPT)
                            stored_prompt = f"SYSTEM:\n{EDIT_DIFF_SYSTEM_PROMPT}\n\nUSER:\n{prompt}"
                            return GeneratedCandidate(raw=raw, prompt=stored_prompt)
                        chosen = consistency.choose_edit(
                            worktree.path,
                            plan,
                            subtask,
                            candidate_generator,
                            model_used=decision.model,
                        )
                    else:
                        prompts = [
                            build_patch_prompt(subtask, context, f"candidate sample {i + 1}")
                            for i in range(consistency.n_samples)
                        ]
                        chosen = consistency.choose(worktree.path, plan, prompts)
                    if chosen.ok:
                        loop_result = type("LoopResultLike", (), {
                            "ok": True, "attempts": chosen.attempts, "diff": chosen.diff,
                            "error": "", "blocked": False,
                        })()
                    else:
                        loop_result = type("LoopResultLike", (), {
                            "ok": False, "attempts": chosen.attempts or consistency.n_samples,
                            "diff": "", "error": chosen.error,
                            "blocked": False,
                        })()
                else:
                    loop = ReliabilityTestLoop(
                        self.tester,
                        builder_engine,
                        max_attempts=self.config["test_loop"]["max_attempts"],
                        model_used=decision.model,
                        patch_generator=patch_generator,
                    )
                    loop_result = loop.run(worktree.path, plan, subtask, context)
                if not loop_result.ok:
                    reason = loop_result.error or "subtask failed"
                    state_store.update_subtask(subtask_id, status="blocked")
                    state_store.record_known_issue(subtask_id, reason, loop_result.error)
                    self.sandbox.rollback(worktree, keep=True)
                    results.append({"id": subtask_id, "status": "blocked", "reason": reason})
                    continue

                regression = self.regression.run(worktree.path)
                if not regression.ok:
                    state_store.update_subtask(subtask_id, status="blocked")
                    state_store.record_known_issue(subtask_id, "regression failed", regression.output)
                    self.sandbox.rollback(worktree, keep=True)
                    results.append({"id": subtask_id, "status": "blocked", "reason": regression.output})
                    continue

                self.sandbox.merge_to_main(worktree)
                state_store.update_subtask(subtask_id, status="done")
                if self.config["worktree_sandbox"].get("cleanup_on_success", True):
                    self.sandbox.rollback(worktree, keep=False)
                results.append({"id": subtask_id, "status": "done"})
            finally:
                state_store.write_project_state(state_workspace)

        status = "done" if all(r["status"] == "done" for r in results) else "partial"
        return {"status": status, "plan": plan, "subtasks": results, "state_workspace": state_workspace}

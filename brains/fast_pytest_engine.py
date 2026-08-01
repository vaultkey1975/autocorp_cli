"""Universal Fast Pytest Engine.

Orchestrates `autocorp_testing` (discovery, change detection, mapping,
safety, history, execution, reporting) into the four commands AutoCorp,
Claude, and Codex use to run the fastest trustworthy tests first:

  test-plan     — report a plan; never runs tests; never writes.
  test-fast     — syntax/import checks + previously-failed/direct/safety
                  tests. Never grants production approval.
  test-focused  — verifies one feature/subsystem. Never grants production
                  approval. Never silently becomes FULL.
  test-full     — runs the repository's own discovered strict command,
                  unmodified. The only mode that can contribute to
                  production approval.
"""

from __future__ import annotations

import os
from typing import Any

from autocorp_testing import change_detection, discovery, execution, history, mapping, planning
from autocorp_testing import safety
from autocorp_testing.schemas import EngineReport, SelectedTest, TestPlan, TestResult


class EngineError(RuntimeError):
    """Raised for a truthful engine or repository-safety failure."""


def _validate_repo(repo_path: str) -> str:
    repo_path = os.path.abspath(repo_path)
    if not os.path.isdir(repo_path):
        raise EngineError(f"repository does not exist: {repo_path}")
    return repo_path


def test_plan(repo_path: str, *, feature: str | None = None, base_branch: str | None = None) -> TestPlan:
    repo_path = _validate_repo(repo_path)
    return planning.build_plan(repo_path, feature=feature, base_branch=base_branch)


def _record_file_history(
    repo_path: str, fp: str, config_fp: str, mode: str, targets: list[str],
    result: execution.PytestRunResult, python_version: str, pytest_version: str | None,
    branch: str = "", commit_sha: str = "",
) -> None:
    per_file_outcomes: dict[str, list[TestResult]] = {}
    for item in result.per_test:
        file_part = item.node_id.split("::", 1)[0]
        per_file_outcomes.setdefault(file_part, []).append(item)
    for target in targets:
        items = per_file_outcomes.get(target, [])
        if items:
            file_result = "failed" if any(i.outcome in ("failed", "error") for i in items) else "passed"
            duration = sum(i.duration_seconds for i in items) or result.duration_seconds / max(len(targets), 1)
            failure_type = next((i.outcome for i in items if i.outcome in ("failed", "error")), None)
        else:
            file_result = "failed" if result.returncode != 0 else "passed"
            duration = result.duration_seconds / max(len(targets), 1)
            failure_type = "failed" if file_result == "failed" else None
        history.record_result(history.HistoryEntry(
            repo_path=repo_path, node_id=target, test_path=target, duration_seconds=duration,
            result=file_result, mode=mode, repository_fingerprint=fp, config_fingerprint=config_fp,
            failure_type=failure_type, python_version=python_version, pytest_version=pytest_version,
            command=result.command, branch=branch, commit_sha=commit_sha,
        ))


def _as_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def _normalize_explicit_paths(repo_path: str, paths: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    normalized: list[str] = []
    accepted: list[str] = []
    rejected: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        candidate = (raw or "").strip()
        if not candidate:
            rejected.append(raw)
            continue
        full = candidate if os.path.isabs(candidate) else os.path.join(repo_path, candidate)
        full = os.path.abspath(full)
        try:
            common = os.path.commonpath([repo_path, full])
        except ValueError:
            common = ""
        rel = os.path.relpath(full, repo_path).replace(os.sep, "/") if common == repo_path else candidate.replace(os.sep, "/")
        normalized.append(rel)
        if common != repo_path or not os.path.exists(full):
            rejected.append(candidate)
            continue
        if rel in seen:
            duplicates.append(rel)
            continue
        seen.add(rel)
        accepted.append(rel)
    return normalized, accepted, rejected, duplicates


def _classify_explicit_path(repo_path: str, rel: str, config) -> dict[str, Any]:
    text = ""
    full = os.path.join(repo_path, rel)
    if os.path.isfile(full):
        try:
            with open(full, encoding="utf-8", errors="ignore") as fh:
                text = fh.read().lower()
        except OSError:
            text = ""
    cls = safety.classify_test(text, config).to_dict()
    base = os.path.basename(rel).lower()
    path_lower = rel.lower()
    fallback_reasons: list[str] = []
    integration = any(token in path_lower for token in ("integration", "workflow", "e2e", "end_to_end"))
    media_heavy = any(token in text or token in path_lower for token in ("ffmpeg", "ffprobe", "wave", "audio", "video", "media", "package"))
    slow = any(token in path_lower for token in ("slow", "workflow", "publish", "publication")) or media_heavy or integration
    if integration:
        fallback_reasons.append("filename/path heuristic: integration/workflow/e2e")
    if media_heavy:
        fallback_reasons.append("filename/content heuristic: media/package work")
    if slow:
        fallback_reasons.append("filename/content heuristic: not expected to be fast")
    cls.update({
        "integration": integration,
        "media_heavy": media_heavy,
        "slow": slow,
        "classification_source": "pytest metadata/content scan with filename heuristic fallback",
    })
    if fallback_reasons:
        cls.setdefault("reasons", [])
        cls["reasons"].extend(fallback_reasons)
    if "test_" not in base:
        cls.setdefault("reasons", [])
        cls["reasons"].append("explicit path may rely on pytest collection rather than filename heuristic")
    return cls


def _explicit_selected(repo_path: str, accepted_paths: list[str], config, config_fp: str) -> list[SelectedTest]:
    selected: list[SelectedTest] = []
    for rel in accepted_paths:
        duration, stale = history.timing_estimate(repo_path, rel, current_config_fingerprint=config_fp)
        cls = _classify_explicit_path(repo_path, rel, config)
        selected.append(SelectedTest(
            node_id=rel,
            path=rel,
            reasons=("explicit --path requested",),
            category="explicit",
            estimated_duration_seconds=duration,
            estimate_is_stale=stale,
            classification=cls,
        ))
    return selected


def _represented_files(results: list[TestResult]) -> list[str]:
    seen: list[str] = []
    for item in results:
        rel = item.node_id.split("::", 1)[0]
        if rel not in seen:
            seen.append(rel)
    return seen


def _performance_warnings(mode: str, duration: float, selected: list[SelectedTest]) -> list[str]:
    warnings: list[str] = []
    if mode == "focused" and duration > 60:
        warnings.append(
            "PERFORMANCE WARNING: Focused verification exceeded 60 seconds. "
            "Review the slow-test report before treating this as a fast development loop."
        )
    heavy = [t.path for t in selected if t.classification.get("slow") or t.classification.get("media_heavy") or t.classification.get("integration")]
    if heavy:
        warnings.append(
            "Explicit selection includes tests classified as integration/media-heavy/not expected to be fast: "
            + ", ".join(heavy[:8])
        )
    return warnings


def _run_selected(
    repo_path: str, plan: TestPlan, config, selected, *, mode: str, extra_deferred: list[str],
    requested_paths: list[str] | None = None, normalized_paths: list[str] | None = None,
    accepted_paths: list[str] | None = None, rejected_paths: list[str] | None = None,
    duplicate_paths: list[str] | None = None, selection_mode: str = "",
) -> EngineReport:
    errors: list[str] = []
    commands: list[list[str]] = []

    changed_py = [f for f in plan.changed_files if f.endswith(".py")]
    compile_result = execution.run_fast_compile_check(repo_path, changed_py) if mode == "fast" else \
        execution.run_full_compile_check(config.compile_command, cwd=repo_path)
    if mode != "fast":
        commands.append(config.compile_command)
    if not compile_result.ok:
        errors.extend(compile_result.errors)

    changed_modules = [
        mapping.module_name(f) for f in changed_py
        if not mapping.is_test_file(f) and os.path.isfile(os.path.join(repo_path, f))
    ]
    import_results = []
    if compile_result.ok and changed_modules:
        import_results = execution.run_import_checks(config.venv_python, repo_path, changed_modules)
        for item in import_results:
            if not item.get("ok"):
                errors.append(f"import check failed for {item.get('module')}: {item.get('error')}")

    targets = [t.path for t in selected]
    per_test_results: list[TestResult] = []
    exit_code = 0
    duration = compile_result.duration_seconds

    if not compile_result.ok:
        exit_code = 1
    elif not targets:
        exit_code = 0
    else:
        run = execution.run_pytest(
            config.venv_python,
            repo_path,
            targets,
            itemized=True,
            extra_args=["--durations=10", "--durations-min=1.0"],
        )
        commands.append(run.command)
        duration += run.duration_seconds
        exit_code = run.returncode
        per_test_results = run.per_test
        if run.timed_out:
            errors.append("pytest run timed out")
        if run.collected == 0:
            exit_code = 1
            errors.append("NOT VERIFIED: pytest collected zero tests for the selected target(s)")
        config_fp = "|".join(sorted(config.config_fingerprint_inputs)) + f"|{config.python_version}|{config.pytest_version}"
        _record_file_history(repo_path, plan.repository_fingerprint, config_fp, mode, targets, run,
                             config.python_version, config.pytest_version, branch=plan.branch, commit_sha=plan.commit_sha)
        collected = run.collected
        skipped = run.skipped
        deselected = run.deselected
        slowest_tests = run.slowest_tests
        represented = _represented_files(per_test_results)
    if not targets or not compile_result.ok:
        collected = 0
        skipped = 0
        deselected = 0
        slowest_tests = []
        represented = []

    passed = sum(1 for r in per_test_results if r.outcome == "passed") if per_test_results else (len(targets) if exit_code == 0 and targets else 0)
    failed = sum(1 for r in per_test_results if r.outcome in ("failed", "error")) if per_test_results else (len(targets) if exit_code != 0 and targets else 0)

    deferred = list(extra_deferred)
    if plan.deferred_to_full:
        deferred.append(f"complete repository suite ({len(plan.deferred_to_full)} additional test file(s) — see test-full)")
    if plan.gpu_safety.get("gpu_tests_detected"):
        deferred.append("real GPU runtime verification (CUDA/Chatterbox/video-model tests deferred)")

    return EngineReport(
        mode=mode, repository=repo_path, repository_fingerprint=plan.repository_fingerprint,
        changed_files=plan.changed_files, selected_tests=selected, commands=commands,
        results=per_test_results, duration_seconds=duration, deferred_checks=deferred,
        parallelism=plan.parallelism, gpu_safety=plan.gpu_safety, database_safety=plan.database_safety,
        errors=errors, passed=passed, failed=failed, exit_code=exit_code,
        compile_result=compile_result.to_dict(), import_check_results=import_results,
        uncertainty_warnings=plan.uncertainty_warnings,
        requested_paths=requested_paths or [],
        normalized_paths=normalized_paths or [],
        accepted_paths=accepted_paths or [],
        rejected_paths=rejected_paths or [],
        duplicate_paths=duplicate_paths or [],
        selection_mode=selection_mode,
        collected=collected,
        skipped=skipped,
        deselected=deselected,
        represented_test_files=represented,
        slowest_tests=slowest_tests,
        performance_warnings=_performance_warnings(mode, duration, selected),
    )


def test_fast(repo_path: str) -> EngineReport:
    repo_path = _validate_repo(repo_path)
    plan = planning.build_plan(repo_path)
    config = discovery.discover(repo_path)
    return _run_selected(repo_path, plan, config, plan.fast_tests, mode="fast", extra_deferred=[])


def test_focused(
    repo_path: str, *, feature: str | None = None, path: str | list[str] | None = None, node_id: str | None = None,
) -> EngineReport:
    repo_path = _validate_repo(repo_path)
    requested_paths = _as_list(path)
    normalized_paths, accepted_paths, rejected_paths, duplicate_paths = _normalize_explicit_paths(repo_path, requested_paths)
    if not (feature or requested_paths or node_id):
        raise EngineError("test-focused requires --feature, --path, or --node-id")
    if rejected_paths:
        return EngineReport(
            mode="focused", repository=repo_path, repository_fingerprint="", changed_files=[],
            selected_tests=[], commands=[], results=[], duration_seconds=0.0,
            deferred_checks=[], parallelism={}, gpu_safety={}, database_safety={},
            errors=[
                "verification did not run because one or more explicit --path values were invalid",
                *[f"invalid explicit path: {p}" for p in rejected_paths],
            ],
            exit_code=2, blocked=True, blocked_reason="Invalid explicit test path(s)",
            requested_paths=requested_paths, normalized_paths=normalized_paths, accepted_paths=accepted_paths,
            rejected_paths=rejected_paths, duplicate_paths=duplicate_paths, selection_mode="explicit-path",
        )
    explicit_paths = accepted_paths or None
    explicit_node_ids = [node_id] if node_id else None
    plan = planning.build_plan(
        repo_path, feature=feature, explicit_paths=explicit_paths, explicit_node_ids=explicit_node_ids,
    )
    config = discovery.discover(repo_path)
    config_fp = "|".join(sorted(config.config_fingerprint_inputs)) + f"|{config.python_version}|{config.pytest_version}"
    if accepted_paths:
        selected = _explicit_selected(repo_path, accepted_paths, config, config_fp)
        selection_mode = "explicit-path"
    else:
        selected = plan.focused_tests or plan.fast_tests
        selection_mode = "feature-or-node"
    return _run_selected(
        repo_path, plan, config, selected, mode="focused", extra_deferred=[],
        requested_paths=requested_paths, normalized_paths=normalized_paths, accepted_paths=accepted_paths,
        rejected_paths=rejected_paths, duplicate_paths=duplicate_paths, selection_mode=selection_mode,
    )


def test_full(repo_path: str) -> EngineReport:
    repo_path = _validate_repo(repo_path)
    config = discovery.discover(repo_path)
    if not config.strict_full_command or config.strict_full_command_confidence == "none":
        return EngineReport(
            mode="full", repository=repo_path, repository_fingerprint="", changed_files=[],
            selected_tests=[], commands=[], results=[], duration_seconds=0.0,
            deferred_checks=[], parallelism={}, gpu_safety={}, database_safety={},
            errors=["could not confidently discover the repository's strict full test command"],
            exit_code=2, blocked=True,
            blocked_reason="Add .autocorp/test-profile.json with \"strict_full_command\" to configure it explicitly.",
        )
    changeset = change_detection.detect_changes(repo_path)
    fp = change_detection.fingerprint(
        changeset, python_version=config.python_version, pytest_version=config.pytest_version,
        config_fingerprint_inputs=config.config_fingerprint_inputs,
    )
    run = execution.run_strict_full(config.strict_full_command, cwd=repo_path)
    return EngineReport(
        mode="full", repository=repo_path, repository_fingerprint=fp,
        changed_files=changeset.changed_files, selected_tests=[], commands=[run.command],
        results=[], duration_seconds=run.duration_seconds, deferred_checks=[],
        parallelism={"enabled": False, "workers": 1, "reason": "FULL always runs sequentially as the repository defines it"},
        gpu_safety={}, database_safety={},
        errors=["pytest run timed out"] if run.timed_out else [],
        passed=run.passed, failed=run.failed, exit_code=run.returncode,
    )

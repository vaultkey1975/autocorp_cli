"""Ties discovery, change detection, mapping, safety, and history together
into a `TestPlan`. Never executes tests and never writes to the target
repository.
"""

from __future__ import annotations

import os
from typing import Any

from autocorp_testing import change_detection, discovery, history, mapping, safety
from autocorp_testing.schemas import SelectedTest, TestPlan

_INTEGRATION_HINTS = ("integration", "_e2e", "end_to_end", "workflow")
_SLOW_HINTS = ("slow", "workflow", "live_test", "publish")


def _selected_test(
    rel: str,
    reasons: list[str],
    category: str,
    *,
    repo_path: str,
    config_fp: str,
    classification: dict[str, bool],
) -> SelectedTest:
    duration, stale = history.timing_estimate(repo_path, rel, current_config_fingerprint=config_fp)
    return SelectedTest(
        node_id=rel, path=rel, reasons=tuple(reasons), category=category,
        estimated_duration_seconds=duration, estimate_is_stale=stale, classification=classification,
    )


def build_plan(
    repo_path: str,
    *,
    feature: str | None = None,
    base_branch: str | None = None,
    explicit_paths: list[str] | None = None,
    explicit_node_ids: list[str] | None = None,
) -> TestPlan:
    repo_path = os.path.abspath(repo_path)
    config = discovery.discover(repo_path)
    changeset = change_detection.detect_changes(
        repo_path, base_branch=base_branch, explicit_paths=explicit_paths, explicit_node_ids=explicit_node_ids,
    )
    fp = change_detection.fingerprint(
        changeset, python_version=config.python_version, pytest_version=config.pytest_version,
        config_fingerprint_inputs=config.config_fingerprint_inputs,
    )
    config_fp = "|".join(sorted(config.config_fingerprint_inputs)) + f"|{config.python_version}|{config.pytest_version}"

    mapping_result, test_index, source_index = mapping.map_changed_files_to_tests(
        repo_path, changeset.changed_files, feature=feature, profile=config.profile,
    )
    safety_map = mapping.mandatory_safety_tests(test_index, config.profile)
    for rel, reasons in safety_map.items():
        for reason in reasons:
            mapping_result.add(rel, reason, "safety")

    previously_failed = set(history.previously_failed(repo_path))

    # Integration heuristic: test files whose name hints at integration and
    # that already matched via another signal (never added on their own).
    matched_rels = set(mapping_result.reasons)
    for rel in list(matched_rels):
        base = os.path.basename(rel).lower()
        if any(h in base for h in _INTEGRATION_HINTS):
            mapping_result.categories[rel] = "integration"

    def build_selected(rels: set[str]) -> list[SelectedTest]:
        out = []
        for rel in sorted(rels):
            tidx = test_index.get(rel)
            text_lower = tidx.text_lower if tidx else ""
            cls = safety.classify_test(text_lower, config)
            category = mapping_result.categories.get(rel, "direct")
            if rel in previously_failed:
                category = "previously_failed"
            reasons = list(mapping_result.reasons.get(rel, []))
            if rel in previously_failed:
                reasons.append("previously failed in AutoCorp's test history for this repository")
            out.append(_selected_test(rel, reasons, category, repo_path=repo_path, config_fp=config_fp, classification=cls.to_dict()))
        return _order_selected(out)

    fast_categories = {"direct", "dependency", "safety"}
    fast_rels = {rel for rel, cat in mapping_result.categories.items() if cat in fast_categories}
    fast_rels |= {rel for rel in matched_rels if any("safety" in r for r in mapping_result.reasons.get(rel, []))}
    fast_tests = build_selected(fast_rels)

    focused_rels = set(fast_rels) | {
        rel for rel, cat in mapping_result.categories.items() if cat in ("feature", "explicit", "integration")
    }
    if explicit_node_ids:
        for nid in explicit_node_ids:
            path = nid.split("::", 1)[0]
            mapping_result.add(path, "explicit --node-id requested", "explicit")
            focused_rels.add(path)
    if explicit_paths:
        for p in explicit_paths:
            mapping_result.add(p, "explicit --path requested", "explicit")
            focused_rels.add(p)
    focused_tests = build_selected(focused_rels) if (feature or explicit_paths or explicit_node_ids) else []

    all_test_rels = set(test_index)
    deferred = sorted(all_test_rels - fast_rels - focused_rels)

    selected_texts = {rel: test_index[rel].text_lower for rel in (fast_rels | focused_rels) if rel in test_index}
    parallelism = safety.decide_parallelism(
        has_xdist=config.has_xdist, selected_texts=selected_texts, cpu_count=os.cpu_count() or 1,
    )

    classified = {rel: safety.classify_test(test_index[rel].text_lower, config) for rel in selected_texts}
    gpu_rels = [rel for rel, c in classified.items() if c.gpu]
    db_rels = [rel for rel, c in classified.items() if c.database]
    gpu_safety = {
        "gpu_tests_detected": gpu_rels,
        "deferred_to_manual_or_full": gpu_rels,
        "note": "FAST/FOCUSED never run real GPU inference (CUDA/Chatterbox/video-model tests are deferred).",
    }
    production_db_paths = config.profile.get("production_db_paths", []) if isinstance(config.profile, dict) else []
    database_safety = {
        "database_tests_detected": db_rels,
        "disposable_data_required": bool(db_rels),
        "production_db_paths_configured": production_db_paths,
    }

    estimated_duration = sum(t.estimated_duration_seconds or 0.75 for t in fast_tests)

    confidence = "high"
    if mapping_result.uncertainty_warnings:
        confidence = "medium"
    if not changeset.changed_files and not feature and not explicit_paths and not explicit_node_ids:
        confidence = "low"

    return TestPlan(
        repository=repo_path,
        repository_fingerprint=fp,
        branch=changeset.branch,
        commit_sha=changeset.commit_sha,
        changed_files=changeset.changed_files,
        discovered_config=config.to_dict(),
        strict_full_command=config.strict_full_command,
        strict_full_command_confidence=config.strict_full_command_confidence,
        compile_command=config.compile_command,
        fast_tests=fast_tests,
        focused_tests=focused_tests,
        deferred_to_full=deferred,
        selection_reasons={t.node_id: list(t.reasons) for t in (fast_tests + focused_tests)},
        estimated_duration_seconds=round(estimated_duration, 3),
        confidence=confidence,
        parallelism=parallelism,
        gpu_safety=gpu_safety,
        database_safety=database_safety,
        uncertainty_warnings=mapping_result.uncertainty_warnings,
    )


_ORDER = {
    "previously_failed": 0,
    "syntax": 1,
    "import": 2,
    "direct": 3,
    "safety": 4,
    "integration": 5,
    "slow": 6,
    "dependency": 7,
    "feature": 3,
    "explicit": 3,
}


def _order_selected(tests: list[SelectedTest]) -> list[SelectedTest]:
    def key(t: SelectedTest):
        base = os.path.basename(t.path).lower()
        is_slow = any(h in base for h in _SLOW_HINTS)
        rank = _ORDER.get("slow" if is_slow and t.category not in ("previously_failed", "safety") else t.category, 5)
        return (rank, t.path)
    return sorted(tests, key=key)

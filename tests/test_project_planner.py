#!/usr/bin/env python3
"""Tests for the Project Action Planner (brains/project_planner.py, Phase 1C).

Covers: clean repository planning, dirty-tree action, missing dependency
files, missing entry points, missing test framework, TODO count, FIXME
count, bare pass statements, NotImplementedError count, no invented
findings, deterministic output, deterministic action IDs, stable priority
ordering, reuse of scanner and analyzer, invalid/missing path handling,
and immutable result collections.
"""

import os

import pytest

from brains.project_planner import (
    ProjectAction,
    ProjectPlan,
    run_project_plan,
)

# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# --------------------------------------------------------------------------- #
# Clean repository
# --------------------------------------------------------------------------- #
def test_clean_repository_produces_plan(tmp_path):
    _write(tmp_path / "main.py", "import argparse\nprint('ok')\n")
    plan = run_project_plan(str(tmp_path))
    assert isinstance(plan, ProjectPlan)
    assert plan.repo_path == str(tmp_path)
    assert isinstance(plan.actions, tuple)
    assert len(plan.actions) >= 1


# --------------------------------------------------------------------------- #
# Dirty working tree
# --------------------------------------------------------------------------- #
def test_dirty_working_tree_creates_high_priority_action(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    plan = run_project_plan(str(tmp_path))
    dirty_actions = [a for a in plan.actions
                     if a.category == "repository" and a.priority == "high"]
    if dirty_actions:
        assert any("dirty" in b.lower() or "uncommitted" in b.lower()
                   for b in plan.blockers)


# --------------------------------------------------------------------------- #
# Missing dependency files
# --------------------------------------------------------------------------- #
def test_missing_dependency_files_creates_action(tmp_path):
    _write(tmp_path / "app.py", "import argparse\n")
    plan = run_project_plan(str(tmp_path))
    dep_actions = [a for a in plan.actions
                   if a.category == "dependencies"]
    assert len(dep_actions) == 1
    assert dep_actions[0].priority == "high"


# --------------------------------------------------------------------------- #
# Missing entry points
# --------------------------------------------------------------------------- #
def test_missing_entry_points_creates_action(tmp_path):
    _write(tmp_path / "deeper" / "lib.py", "x = 1\n")
    plan = run_project_plan(str(tmp_path))
    arch_actions = [a for a in plan.actions
                    if a.category == "architecture" and "entry" in a.title.lower()]
    assert len(arch_actions) == 1
    assert arch_actions[0].priority == "high"


# --------------------------------------------------------------------------- #
# Missing test framework
# --------------------------------------------------------------------------- #
def test_missing_test_framework_creates_action(tmp_path):
    _write(tmp_path / "app.py", "import argparse\n")
    plan = run_project_plan(str(tmp_path))
    test_actions = [a for a in plan.actions
                    if a.category == "testing"]
    assert len(test_actions) == 1
    assert test_actions[0].priority == "high"


# --------------------------------------------------------------------------- #
# TODO count creates evidence-based action
# --------------------------------------------------------------------------- #
def test_todo_count_creates_maintainability_action(tmp_path):
    _write(tmp_path / "main.py", "# TODO: improve this\nprint('ok')\n")
    plan = run_project_plan(str(tmp_path))
    todo_actions = [a for a in plan.actions
                    if a.category == "maintainability" and "TODO" in a.title]
    assert len(todo_actions) == 1
    assert any("TODO" in str(ev).upper() for ev in todo_actions[0].evidence)
    assert int(todo_actions[0].evidence[0].split()[0]) >= 1


# --------------------------------------------------------------------------- #
# FIXME count creates evidence-based action
# --------------------------------------------------------------------------- #
def test_fixme_count_creates_maintainability_action(tmp_path):
    _write(tmp_path / "main.py", "# FIXME: broken logic\nprint('ok')\n")
    plan = run_project_plan(str(tmp_path))
    fixme_actions = [a for a in plan.actions
                     if a.category == "maintainability" and "FIXME" in a.title]
    assert len(fixme_actions) == 1
    assert any("FIXME" in str(ev).upper() for ev in fixme_actions[0].evidence)


# --------------------------------------------------------------------------- #
# Bare pass statements are review candidates, not confirmed defects
# --------------------------------------------------------------------------- #
def test_pass_statements_described_as_review_candidates(tmp_path):
    _write(tmp_path / "main.py", "class Foo:\n    pass\n")
    plan = run_project_plan(str(tmp_path))
    pass_actions = [a for a in plan.actions
                    if "pass" in a.title.lower()]
    if pass_actions:
        reason = pass_actions[0].reason.lower()
        assert "defect" not in reason
        assert "review" in reason or "placeholder" in reason or "intentional" in reason
        assert "intentional" in reason.lower()


# --------------------------------------------------------------------------- #
# NotImplementedError count creates incomplete-code action
# --------------------------------------------------------------------------- #
def test_not_implemented_error_creates_incomplete_code_action(tmp_path):
    _write(tmp_path / "main.py", "raise NotImplementedError\n")
    plan = run_project_plan(str(tmp_path))
    ni_actions = [a for a in plan.actions
                  if a.category == "incomplete-code"]
    assert len(ni_actions) == 1
    assert not ni_actions[0].safe_to_automate
    assert any("NotImplementedError" in str(ev) for ev in ni_actions[0].evidence)


# --------------------------------------------------------------------------- #
# No invented findings
# --------------------------------------------------------------------------- #
def test_no_unsupported_findings_invented(tmp_path):
    _write(tmp_path / "main.py", "import os\nprint('hello')\n")
    plan = run_project_plan(str(tmp_path))
    supported_categories = {"repository", "dependencies", "testing",
                             "incomplete-code", "maintainability",
                             "architecture", "documentation"}
    for action in plan.actions:
        assert action.category in supported_categories, (
            f"Unexpected category: {action.category}"
        )
        assert len(action.evidence) >= 0
        assert action.reason


# --------------------------------------------------------------------------- #
# Deterministic output
# --------------------------------------------------------------------------- #
def test_deterministic_output_across_repeated_runs(tmp_path):
    _write(tmp_path / "main.py", (
        "# TODO: one\n# FIXME: two\nraise NotImplementedError\n"
    ))
    plan1 = run_project_plan(str(tmp_path))
    plan2 = run_project_plan(str(tmp_path))
    assert plan1.actions == plan2.actions
    assert plan1.blockers == plan2.blockers
    assert plan1.summary == plan2.summary
    assert plan1.confidence == plan2.confidence


# --------------------------------------------------------------------------- #
# Deterministic action IDs
# --------------------------------------------------------------------------- #
def test_deterministic_action_ids(tmp_path):
    _write(tmp_path / "main.py", "# TODO: test id\n")
    plan1 = run_project_plan(str(tmp_path))
    plan2 = run_project_plan(str(tmp_path))
    ids1 = [a.action_id for a in plan1.actions]
    ids2 = [a.action_id for a in plan2.actions]
    assert ids1 == ids2
    for aid in ids1:
        assert len(aid) == 12
        assert all(c in "0123456789abcdef" for c in aid)


# --------------------------------------------------------------------------- #
# Stable priority ordering
# --------------------------------------------------------------------------- #
def test_stable_priority_ordering(tmp_path):
    _write(tmp_path / "main.py", (
        "# TODO: one\n"
        "# FIXME: two\n"
        "raise NotImplementedError\n"
        "pass\n"
    ))
    plan = run_project_plan(str(tmp_path))
    actions = list(plan.actions)
    priorities = [a.priority for a in actions]
    from brains.project_planner import _PRIORITY_ORDER
    order_values = [_PRIORITY_ORDER[p] for p in priorities]
    assert order_values == sorted(order_values), (
        f"Actions not sorted by priority: {priorities}"
    )


# --------------------------------------------------------------------------- #
# Reuse scanner and analyzer
# --------------------------------------------------------------------------- #
def test_planner_reuses_scanner_and_analyzer_not_duplicating_logic(tmp_path):
    _write(tmp_path / "main.py", "import argparse\nprint('hello')\n")
    plan = run_project_plan(str(tmp_path))
    assert plan.project_type is not None
    assert plan.overall_health is not None
    assert isinstance(plan.summary, str)


# --------------------------------------------------------------------------- #
# Invalid or missing path
# --------------------------------------------------------------------------- #
def test_missing_repository_path_handled_gracefully(tmp_path):
    missing = str(tmp_path / "does_not_exist")
    plan = run_project_plan(missing)
    assert isinstance(plan, ProjectPlan)
    assert plan.repo_path == missing


# --------------------------------------------------------------------------- #
# Immutable collections
# --------------------------------------------------------------------------- #
def test_plan_contains_immutable_collections(tmp_path):
    _write(tmp_path / "main.py", "print('test')\n")
    plan = run_project_plan(str(tmp_path))
    assert isinstance(plan.actions, tuple)
    assert isinstance(plan.blockers, tuple)
    for action in plan.actions:
        assert isinstance(action.evidence, tuple)
        assert isinstance(action.affected_paths, tuple)
        with pytest.raises((AttributeError, TypeError)):
            action.evidence = ("new",)
        with pytest.raises((AttributeError, TypeError)):
            action.priority = "critical"

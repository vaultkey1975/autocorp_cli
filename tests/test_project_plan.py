"""Tests for the ProjectPlan contract and its sanitisers (brains/project_plan)."""

import pytest

from brains.project_plan import (
    ProjectPlan,
    PlanValidationError,
    sanitize_name,
    safe_relpath,
)


def test_sanitize_name():
    assert sanitize_name("My Cool App!") == "my_cool_app"
    assert sanitize_name("  ") == "project"
    assert sanitize_name("") == "project"
    assert sanitize_name("keep-dash_and1") == "keep-dash_and1"
    # Capped at 50 characters.
    assert len(sanitize_name("a" * 100)) == 50


def test_safe_relpath_rejects_escapes():
    assert safe_relpath("/etc/passwd") == "etc/passwd"
    assert safe_relpath("../../secret") == "secret"
    assert safe_relpath("a/./b/../c") == "a/b/c"
    assert safe_relpath("ui/main.py") == "ui/main.py"
    assert safe_relpath("") == ""


def test_from_dict_fills_safe_defaults():
    plan = ProjectPlan.from_dict({})
    # ensure_buildable() guarantees a runnable shape.
    assert plan.files == [{"path": "main.py", "purpose": "program entry point"}]
    assert plan.build_order == ["main.py"]
    assert plan.test_command == "python -m pytest -q"
    assert plan.success_criteria
    assert plan.is_valid


def test_from_dict_reconciles_build_order():
    data = {
        "project_name": "demo",
        "files": [{"path": "a.py"}, {"path": "b.py"}],
        # model listed only b.py (plus a bogus entry that must be dropped)
        "build_order": ["b.py", "ghost.py"],
    }
    plan = ProjectPlan.from_dict(data)
    # known entries kept in model order, then any forgotten files appended;
    # unknown entries dropped.
    assert plan.build_order == ["b.py", "a.py"]


def test_from_dict_sanitises_paths():
    plan = ProjectPlan.from_dict({"files": [{"path": "../../evil.py"}]})
    assert plan.files[0]["path"] == "evil.py"


def test_validate_reports_problems_and_can_raise():
    bad = ProjectPlan()  # empty files / build_order / test_command / criteria
    errors = bad.validate()
    assert errors  # non-empty list of human-readable problems
    with pytest.raises(PlanValidationError):
        bad.validate(strict=True)


def test_to_dict_round_trips_keys():
    plan = ProjectPlan.from_dict({"project_name": "demo", "language": "python"})
    d = plan.to_dict()
    for key in (
        "project_name", "project_type", "language", "summary", "files",
        "build_order", "test_command", "success_criteria", "steps",
    ):
        assert key in d

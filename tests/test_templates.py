"""Tests for the deterministic template layer (brains/templates).

Locks in template routing and the PySide6 Validation Upgrade: the acceptance
command must execute main.py headless, and the generated main.py must carry the
imports + offscreen auto-quit that make `QT_QPA_PLATFORM=offscreen python main.py`
exit 0 instead of blocking.
"""

from brains.templates import select_template
from brains.templates import pyside6_desktop as tpl

PYSIDE_FILES = {"requirements.txt", "ui/__init__.py", "ui/main_window.py", "main.py"}
EXPECTED_TEST_COMMAND = "QT_QPA_PLATFORM=offscreen python main.py"


def _main_purpose(plan):
    return next(f["purpose"] for f in plan["files"] if f["path"] == "main.py")


def test_select_template_matches_gui_requests():
    for request in (
        "build a desktop calculator",
        "a GUI todo app",
        "a PySide6 inventory manager",
        "make a Qt window",
    ):
        chosen = select_template(request)
        assert chosen is not None, request
        assert chosen.NAME == "pyside6_desktop"


def test_select_template_returns_none_for_non_gui():
    for request in (
        "a python library of string helpers",
        "a command-line argument parser",
        "a function that reverses a list",
    ):
        assert select_template(request) is None, request


def test_matches_keywords():
    assert tpl.matches("a desktop app") is True
    assert tpl.matches("PYSIDE6 dashboard") is True
    assert tpl.matches("a REST api") is False
    assert tpl.matches("") is False


def test_build_plan_shape():
    plan = tpl.build_plan("build a desktop calculator")
    for key in (
        "project_name", "project_type", "language", "files",
        "build_order", "test_command", "success_criteria",
    ):
        assert key in plan
    assert plan["project_name"] == "calculator_app"
    assert plan["language"] == "python"
    assert {f["path"] for f in plan["files"]} == PYSIDE_FILES


def test_build_order_is_dependency_safe():
    plan = tpl.build_plan("a GUI todo app")
    assert plan["build_order"] == [
        "requirements.txt", "ui/__init__.py", "ui/main_window.py", "main.py",
    ]
    # main.py imports from ui.main_window, so it must be built last.
    order = plan["build_order"]
    assert order.index("ui/main_window.py") < order.index("main.py")


def test_test_command_executes_main():
    plan = tpl.build_plan("build a desktop calculator")
    assert plan["test_command"] == EXPECTED_TEST_COMMAND
    assert tpl.TEST_COMMAND == EXPECTED_TEST_COMMAND


def test_no_legacy_launch_test_constant():
    # The old import-and-show smoke test was removed in the Validation Upgrade.
    assert not hasattr(tpl, "LAUNCH_TEST")


def test_main_py_purpose_locks_validation_upgrade():
    purpose = _main_purpose(tpl.build_plan("build a desktop calculator"))
    for token in ("import sys", "import os", "QApplication", "QTimer",
                  "from ui.main_window import MainWindow"):
        assert token in purpose, token
    # The offscreen-only auto-quit is what makes the headless run exit 0.
    assert "QT_QPA_PLATFORM" in purpose
    assert "offscreen" in purpose
    assert "singleShot" in purpose


def test_project_name_derivation():
    assert tpl.build_plan("a GUI todo app")["project_name"] == "todo_app"
    # A request with no usable words still yields a safe, non-empty name.
    name = tpl.build_plan("a desktop app")["project_name"]
    assert name and name.isidentifier()

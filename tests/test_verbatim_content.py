"""Tests for the deterministic verbatim-content channel.

ProjectPlan must preserve a file's exact `content` through from_dict/to_dict, and
the Builder must write that content verbatim WITHOUT calling the engine. This is
what makes the SQLite persistence/export layer guaranteed-correct regardless of the
model (the model previously paraphrased an embedded INSERT into a bug).
"""

from brains.project_plan import ProjectPlan
from brains.builder import BuilderBrain
from brains.templates import sqlite_desktop as sql
from safety.executor import Executor
from safety.gate import AllowAllGate


class _RecordingEngine:
    """Stand-in engine that records prompts and returns a fixed marker, so tests
    never touch Ollama."""

    name = "recording"

    def __init__(self):
        self.calls = []

    def generate(self, prompt, system=""):
        self.calls.append(prompt)
        return "ENGINE-GENERATED\n"


def test_projectplan_preserves_content_round_trip():
    raw = {
        "project_name": "demo",
        "files": [
            {"path": "fixed.py", "purpose": "p", "content": "EXACT = 1\n"},
            {"path": "free.py", "purpose": "make it"},
        ],
        "build_order": ["fixed.py", "free.py"],
        "test_command": "python -m pytest -q",
        "success_criteria": ["ok"],
    }
    out = ProjectPlan.from_dict(raw).to_dict()
    fixed = next(f for f in out["files"] if f["path"] == "fixed.py")
    free = next(f for f in out["files"] if f["path"] == "free.py")
    assert fixed["content"] == "EXACT = 1\n"
    assert "content" not in free  # files without content stay content-less


def test_blank_content_is_ignored():
    out = ProjectPlan.from_dict(
        {"files": [{"path": "a.py", "purpose": "x", "content": "   "}]}
    ).to_dict()
    assert "content" not in out["files"][0]


def test_builder_writes_content_verbatim_and_skips_engine(tmp_path):
    engine = _RecordingEngine()
    builder = BuilderBrain(Executor(AllowAllGate()), engine=engine)
    plan = {
        "project_name": "demo", "language": "python", "summary": "s",
        "files": [
            {"path": "fixed.py", "purpose": "p", "content": "EXACT = 42\n"},
            {"path": "free.py", "purpose": "generate me"},
        ],
        "build_order": ["fixed.py", "free.py"],
    }
    builder.build(plan, str(tmp_path))
    assert (tmp_path / "fixed.py").read_text() == "EXACT = 42\n"
    assert (tmp_path / "free.py").read_text() == "ENGINE-GENERATED\n"
    # The engine was called exactly once — only for the non-content file.
    assert len(engine.calls) == 1
    assert "free.py" in engine.calls[0]


def test_sqlite_template_marks_persistence_files_verbatim():
    plan = sql.build_plan("Build a customer CRM desktop app with SQLite")
    by_path = {f["path"]: f for f in plan["files"]}
    # Deterministic files carry exact content...
    for path in ("database.py", "crud.py", "export.py", "main.py",
                 "requirements.txt", "ui/__init__.py"):
        assert by_path[path].get("content"), path
    # ...the UI is left for the engine.
    assert "content" not in by_path["ui/main_window.py"]
    # The exact crud INSERT is correct: 3 placeholders for 3 columns (the bug the
    # model introduced when this was only a prose purpose).
    assert "VALUES (?, ?, ?)" in by_path["crud.py"]["content"]


def test_sqlite_content_survives_planner_round_trip():
    plan = sql.build_plan("Build a customer CRM desktop app with SQLite")
    out = ProjectPlan.from_dict(plan).to_dict()
    crud = next(f for f in out["files"] if f["path"] == "crud.py")
    assert "def add_customer" in crud["content"]
    assert "VALUES (?, ?, ?)" in crud["content"]

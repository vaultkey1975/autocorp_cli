"""Tests for the SQLite memory store (memory/store).

The store reads its DB location from module-level DB_PATH / DATA_DIR (imported
from config). We monkeypatch those onto a temp directory so tests never touch the
real data/autocorp.db.
"""

import pytest

from memory import store


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    store.init_db()
    return store


def test_record_build_and_recent(temp_store):
    plan = {"project_name": "demo", "files": [{"path": "main.py"}]}
    rowid = temp_store.record_build(
        "build a demo", "demo", "/ws/demo", plan, status="passed", summary="ok"
    )
    assert rowid > 0
    recent = temp_store.recent_builds()
    assert len(recent) == 1
    assert recent[0]["project_name"] == "demo"
    assert recent[0]["status"] == "passed"


def test_record_and_recall_lesson(temp_store):
    temp_store.record_lesson(
        kind="fix",
        title="Fixed main.py in calculator",
        problem="NameError: name 'sys' is not defined",
        solution="add 'import sys' at the top of main.py",
        tags="python calculator main.py",
    )
    hits = temp_store.recall_lessons("calculator main.py NameError")
    assert hits
    assert any("calculator" in h["title"].lower() for h in hits)


def test_recall_ranks_more_relevant_first(temp_store):
    temp_store.record_lesson(kind="fix", title="todo widget tweak",
                             solution="x", tags="todo widget")
    temp_store.record_lesson(kind="fix", title="calculator grid layout fix",
                             solution="y", tags="python calculator grid layout")
    hits = temp_store.recall_lessons("python calculator grid layout")
    assert hits
    assert "calculator" in hits[0]["title"].lower()


def test_recall_empty_for_no_keywords(temp_store):
    assert temp_store.recall_lessons("a an the") == []


def test_stats_counts(temp_store):
    temp_store.record_build("r", "p", "/ws/p", {"x": 1}, status="passed")
    temp_store.record_lesson(kind="success", title="t")
    temp_store.record_lesson(kind="fix", title="t2")
    s = temp_store.stats()
    assert s["builds"] == 1
    assert s["lessons"] == 2
    assert s["successes"] == 1
    assert s["fixes"] == 1


def test_format_lessons_for_prompt(temp_store):
    assert temp_store.format_lessons_for_prompt([]) == ""
    block = temp_store.format_lessons_for_prompt(
        [{"kind": "fix", "title": "Fixed X", "solution": "do Y"}]
    )
    assert "Fixed X" in block
    assert "do Y" in block

#!/usr/bin/env python3
"""
Reviewer storage tests  (AutoCorp CLI - Phase 8B RED)
=====================================================

Drives the additive `reviews` persistence on the memory store, following the
existing `plans` table precedent (CREATE TABLE IF NOT EXISTS, best-effort writes
that never raise). RED: `store.record_review` / `store.recent_reviews` do not
exist yet.

Fully offline: SQLite on a temp DB, no network. The DB location is monkeypatched
onto tmp_path exactly like test_memory_store.py, so the real data/autocorp.db is
never touched.
"""

import pytest

from memory import store


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    store.init_db()
    return store


def _report_dict(name="demo", score=80):
    return {
        "project_name": name,
        "workspace": f"/ws/{name}",
        "score": score,
        "summary": f"{name} review · score {score}/100",
        "findings": [{
            "file": "main.py", "line": 1, "severity": "error",
            "category": "missing_import", "symbol": "sqlite3",
            "message": "Name 'sqlite3' used but never imported.",
            "source": "static",
        }],
    }


def test_record_review_returns_id(temp_store):
    rowid = temp_store.record_review(_report_dict())
    assert isinstance(rowid, int)
    assert rowid > 0


def test_recent_reviews_returns_recorded(temp_store):
    temp_store.record_review(_report_dict(name="alpha", score=72))
    rows = temp_store.recent_reviews()
    assert any(r["project_name"] == "alpha" for r in rows)
    alpha = next(r for r in rows if r["project_name"] == "alpha")
    assert alpha["score"] == 72


def test_recent_reviews_newest_first(temp_store):
    temp_store.record_review(_report_dict(name="first", score=50))
    temp_store.record_review(_report_dict(name="second", score=90))
    rows = temp_store.recent_reviews()
    assert rows[0]["project_name"] == "second"


def test_record_review_bad_input_returns_minus_one(temp_store):
    assert temp_store.record_review(None) == -1
    assert temp_store.record_review(123) == -1


def test_init_db_idempotent_with_reviews(temp_store):
    # Re-running init_db must not error and the reviews table must keep working.
    temp_store.init_db()
    temp_store.init_db()
    rowid = temp_store.record_review(_report_dict(name="again"))
    assert rowid > 0

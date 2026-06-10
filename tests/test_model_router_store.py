#!/usr/bin/env python3
"""
Model Router storage tests  (AutoCorp CLI - Phase 8C RED)
=========================================================

Drives the additive `routes` decision-log on the memory store, following the
`plans`/`reviews` precedent (CREATE TABLE IF NOT EXISTS, best-effort writes that
never raise). RED: `store.record_route_decision` / `store.recent_routes` do not
exist yet.

Fully offline: SQLite on a temp DB (monkeypatched like test_memory_store.py), no
network.
"""

import pytest

from memory import store


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "test.db"))
    store.init_db()
    return store


def _decision(engine="local", rule="fallback", reason="r", fallback_used=False):
    return {
        "engine": engine, "rule": rule, "reason": reason,
        "fallback_used": fallback_used,
    }


def test_record_route_decision_returns_id(temp_store):
    rowid = temp_store.record_route_decision(
        _decision(engine="claude", rule="r1"),
        request="build an api", project_name="demo",
    )
    assert isinstance(rowid, int)
    assert rowid > 0


def test_recent_routes_returns_recorded(temp_store):
    temp_store.record_route_decision(
        _decision(engine="claude", rule="apis"),
        request="build an api", project_name="alpha",
    )
    rows = temp_store.recent_routes()
    assert any(r["project_name"] == "alpha" for r in rows)
    alpha = next(r for r in rows if r["project_name"] == "alpha")
    assert alpha["engine"] == "claude"


def test_recent_routes_newest_first(temp_store):
    temp_store.record_route_decision(_decision(engine="local"), project_name="first")
    temp_store.record_route_decision(_decision(engine="claude"), project_name="second")
    rows = temp_store.recent_routes()
    assert rows[0]["project_name"] == "second"


def test_record_route_decision_bad_input_returns_minus_one(temp_store):
    assert temp_store.record_route_decision(None) == -1
    assert temp_store.record_route_decision(123) == -1


def test_init_db_idempotent_with_routes(temp_store):
    temp_store.init_db()
    temp_store.init_db()
    rowid = temp_store.record_route_decision(_decision(), project_name="again")
    assert rowid > 0

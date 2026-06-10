#!/usr/bin/env python3
"""
Agent Team Templates: selection  (AutoCorp CLI - Phase 8E RED)
==============================================================

Drives `select_team_profile(request)`: a deterministic mapping from a request to
the right team profile (or None when nothing matches, so the global defaults
stand). RED: the accessor does not exist yet.

Fully offline: pure request classification; no engine, no model, no network. The
accessor is imported lazily so each test fails individually.
"""

import pytest


def _select(request):
    from brains.templates import select_team_profile
    return select_team_profile(request)


CLI_REQ = "build a CLI tool to rename files in a folder"
DESKTOP_REQ = "build a desktop calculator GUI window"
SQLITE_REQ = "build a customer CRM desktop app backed by SQLite"
DASHBOARD_REQ = "build a sales metrics dashboard with charts"
UNKNOWN_REQ = "build a library that parses ISO date strings"


def test_cli_request_selects_cli_team():
    assert "cli" in _select(CLI_REQ)["name"].lower()


def test_desktop_request_selects_pyside6_team():
    assert "pyside6" in _select(DESKTOP_REQ)["name"].lower()


def test_sqlite_request_selects_sqlite_team():
    name = _select(SQLITE_REQ)["name"].lower()
    assert "sqlite" in name
    assert "dashboard" not in name


def test_dashboard_request_selects_dashboard_team():
    assert "dashboard" in _select(DASHBOARD_REQ)["name"].lower()


def test_unknown_request_returns_none():
    assert _select(UNKNOWN_REQ) is None


def test_selection_is_deterministic():
    assert _select(SQLITE_REQ) == _select(SQLITE_REQ)
    assert _select(DASHBOARD_REQ) == _select(DASHBOARD_REQ)


def test_none_or_empty_request_returns_none_without_raising():
    assert _select(None) is None
    assert _select("") is None

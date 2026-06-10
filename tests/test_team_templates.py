#!/usr/bin/env python3
"""
Agent Team Templates: profile schema  (AutoCorp CLI - Phase 8E RED)
==================================================================

Drives the design of per-project-type "team profiles": data bundles that pair a
template with its recommended engine routing, reviewer emphasis, and acceptance
criteria. Profiles are consumed through the EXISTING 8B/8C seams (ReviewerBrain
config, ModelRouter rule shape) - this phase adds data, not new mechanism.

Contract under test (none of it exists yet -> RED):
  * brains.templates.select_team_profile(request) -> dict | None
  * each existing template module exposes a TEAM_PROFILE dict
  * a team profile has: name, route_rules (8C Rule shape), review_profile,
    acceptance (list[str])

Fully offline: pure data validation; no engine, no model, no network. Profiles
are imported lazily so each test fails individually rather than as a collection
error.
"""

import pytest


# --------------------------------------------------------------------------- #
# Lazy accessors + shared validator
# --------------------------------------------------------------------------- #
def _select(request):
    from brains.templates import select_team_profile
    return select_team_profile(request)


# Representative, unambiguous requests for each team type.
CLI_REQ = "build a CLI tool to rename files in a folder"
DESKTOP_REQ = "build a desktop calculator GUI window"
SQLITE_REQ = "build a customer CRM desktop app backed by SQLite"
DASHBOARD_REQ = "build a sales metrics dashboard with charts"


def _assert_valid_profile(p):
    assert isinstance(p, dict), "profile must be a dict"
    assert isinstance(p.get("name"), str) and p["name"].strip(), "name required"

    rules = p.get("route_rules")
    assert isinstance(rules, list), "route_rules must be a list"
    for r in rules:
        assert isinstance(r, dict)
        for key in ("name", "engine", "reason", "match"):
            assert key in r, f"rule missing '{key}'"
        assert isinstance(r["match"], dict), "rule.match must be a dict"

    rp = p.get("review_profile")
    assert isinstance(rp, dict), "review_profile must be a dict"
    if "large_function_lines" in rp:
        assert isinstance(rp["large_function_lines"], int)
    if "emphasize" in rp:
        assert isinstance(rp["emphasize"], list)
        assert all(isinstance(x, str) for x in rp["emphasize"])

    acc = p.get("acceptance")
    assert isinstance(acc, list) and acc, "acceptance must be a non-empty list"
    assert all(isinstance(x, str) and x.strip() for x in acc)


# --------------------------------------------------------------------------- #
# Per-type schema validity
# --------------------------------------------------------------------------- #
def test_cli_team_profile_schema():
    _assert_valid_profile(_select(CLI_REQ))


def test_pyside6_team_profile_schema():
    _assert_valid_profile(_select(DESKTOP_REQ))


def test_sqlite_team_profile_schema():
    _assert_valid_profile(_select(SQLITE_REQ))


def test_dashboard_team_profile_schema():
    _assert_valid_profile(_select(DASHBOARD_REQ))


# --------------------------------------------------------------------------- #
# Field-level validation
# --------------------------------------------------------------------------- #
def test_route_rules_use_8c_rule_shape():
    # Every rule must be constructable as a real 8C Rule (consumed seam, no change).
    from brains.model_router import Rule
    rules = _select(SQLITE_REQ)["route_rules"]
    for r in rules:
        rule = Rule(**r)
        assert rule.engine and isinstance(rule.match, dict)


def test_review_profile_validation():
    rp = _select(DASHBOARD_REQ)["review_profile"]
    assert isinstance(rp, dict)
    if "large_function_lines" in rp:
        assert isinstance(rp["large_function_lines"], int)
        assert rp["large_function_lines"] > 0


def test_acceptance_criteria_validation():
    acc = _select(SQLITE_REQ)["acceptance"]
    assert isinstance(acc, list) and acc
    assert all(isinstance(x, str) and x.strip() for x in acc)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_profile_round_trip_is_deterministic():
    assert _select(SQLITE_REQ) == _select(SQLITE_REQ)


# --------------------------------------------------------------------------- #
# Profiles attached to the existing template modules
# --------------------------------------------------------------------------- #
def test_sqlite_desktop_module_has_team_profile():
    from brains.templates import sqlite_desktop
    _assert_valid_profile(getattr(sqlite_desktop, "TEAM_PROFILE", None))


def test_pyside6_desktop_module_has_team_profile():
    from brains.templates import pyside6_desktop
    _assert_valid_profile(getattr(pyside6_desktop, "TEAM_PROFILE", None))

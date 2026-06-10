#!/usr/bin/env python3
"""
Project templates  (AutoCorp CLI - brains.templates)
====================================================

A template recognises a kind of request (e.g. a PySide6 desktop app) and returns
a deterministic, structured plan for it - the exact files, purposes, and a
dependency-safe build order - so the Planner doesn't have to rely on the model to
get the project's *shape* right.

A template module exposes:
    NAME            : str
    matches(request) -> bool
    build_plan(request) -> dict      # a ProjectPlan-shaped dict

`select_template(request)` returns the first matching template module, or None
(in which case the Planner falls back to its normal LLM planning - unchanged).
"""

import copy

from brains.templates import sqlite_desktop
from brains.templates import pyside6_desktop

# Order matters: the first template that matches wins. sqlite_desktop is listed
# before pyside6_desktop because a SQLite desktop request ("...desktop app with
# SQLite") also contains the generic desktop/app keywords pyside6_desktop matches;
# the more specific (data + GUI) template must get first refusal.
_TEMPLATES = [sqlite_desktop, pyside6_desktop]


def select_template(request: str):
    """Return the first template whose matches() accepts the request, or None."""
    for template in _TEMPLATES:
        try:
            if template.matches(request):
                return template
        except Exception:  # noqa: BLE001 - a broken matcher must not break planning
            continue
    return None


# --------------------------------------------------------------------------- #
# Agent Team profiles (Phase 8E)
# --------------------------------------------------------------------------- #
# CLI and Dashboard requests match neither template module today, so their team
# profiles live here as DATA (no new template system). Each profile is consumed
# through existing seams: route_rules -> ModelRouter, review_profile ->
# ReviewerBrain, acceptance -> plan success_criteria.
_CLI_TEAM_PROFILE = {
    "name": "cli_tool_team",
    "route_rules": [
        {"name": "cli-large-to-claude", "engine": "claude",
         "reason": "large multi-file CLI tools benefit from a stronger model",
         "match": {"min_files": 4}},
    ],
    "review_profile": {"emphasize": ["missing_import", "syntax_error"]},
    "acceptance": [
        "pytest reports all tests passing",
        "the CLI entry point runs without error",
    ],
}

_DASHBOARD_TEAM_PROFILE = {
    "name": "dashboard_team",
    "route_rules": [
        {"name": "dashboard-complex-to-claude", "engine": "claude",
         "reason": "chart/dashboard assembly benefits from a stronger model",
         "match": {"min_files": 6}},
    ],
    "review_profile": {
        "large_function_lines": 100,
        "emphasize": ["large_function", "missing_import"],
    },
    "acceptance": [
        "pytest reports all tests passing",
        "the dashboard imports without error",
        "chart and report exports are present",
    ],
}

# Keyword groups for the two types without a template module. Checked in a fixed
# order so selection is deterministic (dashboard before sqlite, since dashboards
# are data+GUI too; cli last).
_DASHBOARD_KEYWORDS = ("dashboard", "charts")
_CLI_KEYWORDS = ("cli", "command-line", "command line", "terminal")


def select_team_profile(request: str):
    """Return the team profile for `request`, or None when nothing matches.

    Deterministic and defensive: None/empty/unknown requests return None; a
    returned profile is a deep copy so callers can never mutate the canonical
    data. No model call."""
    text = (request or "").strip().lower()
    if not text:
        return None

    # Dashboard before SQLite: a dashboard app is data+GUI too.
    if any(k in text for k in _DASHBOARD_KEYWORDS):
        return copy.deepcopy(_DASHBOARD_TEAM_PROFILE)
    try:
        if sqlite_desktop.matches(request):
            return copy.deepcopy(sqlite_desktop.TEAM_PROFILE)
        if pyside6_desktop.matches(request):
            return copy.deepcopy(pyside6_desktop.TEAM_PROFILE)
    except Exception:  # noqa: BLE001 - a broken matcher must never raise here
        pass
    if any(k in text for k in _CLI_KEYWORDS):
        return copy.deepcopy(_CLI_TEAM_PROFILE)
    return None


def merge_acceptance(plan: dict, profile) -> dict:
    """Return a copy of `plan` whose success_criteria has the profile's acceptance
    items appended (order preserved, de-duplicated). A None/malformed profile is a
    no-op. Never raises; never mutates the input plan."""
    out = dict(plan or {})
    merged = list(out.get("success_criteria") or [])
    if isinstance(profile, dict):
        acceptance = profile.get("acceptance")
        if isinstance(acceptance, list):
            for item in acceptance:
                if isinstance(item, str) and item.strip() and item not in merged:
                    merged.append(item)
    out["success_criteria"] = merged
    return out

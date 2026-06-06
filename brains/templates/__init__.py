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

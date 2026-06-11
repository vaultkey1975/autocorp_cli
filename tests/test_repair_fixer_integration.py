#!/usr/bin/env python3
"""
RepairTask -> Fixer request tests  (AutoCorp CLI - Phase 8J RED)
===============================================================

Drives the design of Phase 8J: converting RepairTask planning objects into
structured FixRequest objects that the existing Fixer can consume in a future
phase. This phase creates CONVERSION objects only - it invokes NO Fixer, executes
NO repairs, and triggers NO retry, rerun, or rebuild.

These tests are RED on purpose:
  * `AcceptanceBrain.to_fix_requests` is a STUB that raises NotImplementedError,
    so every test that calls it fails until GREEN implements it.
  * `FixRequest` does not exist yet - it is lazily imported inside `_FixRequest()`
    so tests fail individually (ImportError) rather than collapsing the module
    into one collection error.

`AcceptanceBrain` and `RepairTask` already exist (Phases 8H/8I) and are imported
normally. Fully offline: pure data, no model, no network.
"""

import pytest

from brains.acceptance_brain import AcceptanceBrain, RepairTask


def _FixRequest():
    """Lazy import: RED until GREEN adds FixRequest to brains.acceptance_brain."""
    from brains.acceptance_brain import FixRequest
    return FixRequest


# --------------------------------------------------------------------------- #
# 1. Single RepairTask generates a Fixer request
# --------------------------------------------------------------------------- #
def test_single_repairtask_generates_fix_request():
    FixRequest = _FixRequest()
    tasks = [RepairTask("Dashboard missing export button")]
    assert AcceptanceBrain().to_fix_requests(tasks) == [
        FixRequest("Dashboard missing export button")
    ]


# --------------------------------------------------------------------------- #
# 2. Multiple RepairTasks generate multiple Fixer requests
# --------------------------------------------------------------------------- #
def test_multiple_repairtasks_generate_multiple_fix_requests():
    FixRequest = _FixRequest()
    tasks = [
        RepairTask("Dashboard missing export button"),
        RepairTask("CSV export not visible"),
    ]
    assert AcceptanceBrain().to_fix_requests(tasks) == [
        FixRequest("Dashboard missing export button"),
        FixRequest("CSV export not visible"),
    ]


# --------------------------------------------------------------------------- #
# 3. Empty RepairTask list generates no Fixer requests
# --------------------------------------------------------------------------- #
def test_empty_repairtask_list_generates_no_fix_requests():
    assert AcceptanceBrain().to_fix_requests([]) == []


# --------------------------------------------------------------------------- #
# 4. AcceptanceBrain exposes a conversion method returning list[FixRequest]
# --------------------------------------------------------------------------- #
def test_to_fix_requests_returns_list_of_fixrequest():
    FixRequest = _FixRequest()
    requests = AcceptanceBrain().to_fix_requests([RepairTask("Dashboard missing export button")])
    assert isinstance(requests, list)
    assert all(isinstance(r, FixRequest) for r in requests)

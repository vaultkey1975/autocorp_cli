#!/usr/bin/env python3
"""
FixRequest -> Fixer handoff tests  (AutoCorp CLI - Phase 8K RED)
===============================================================

Drives the design of Phase 8K: the first execution-ADJACENT seam between planning
objects and the existing Fixer. FixRequest objects are converted into FixerWorkItem
objects that the Fixer could consume in a future phase.

This phase does NOT invoke the Fixer, execute repairs, modify orchestrator flow,
or create retry / self-healing behaviour - it only SHAPES the handoff.

These tests are RED on purpose:
  * `AcceptanceBrain.to_fixer_work_items` is a STUB that raises NotImplementedError,
    so every test that calls it fails until GREEN implements it.
  * `FixerWorkItem` does not exist yet - it is lazily imported inside
    `_FixerWorkItem()` so tests fail individually (ImportError) rather than
    collapsing the module into one collection error.

`AcceptanceBrain` and `FixRequest` already exist (Phases 8H/8J) and are imported
normally. Fully offline: pure data, no model, no network.
"""

import pytest

from brains.acceptance_brain import AcceptanceBrain, FixRequest


def _FixerWorkItem():
    """Lazy import: RED until GREEN adds FixerWorkItem to brains.acceptance_brain."""
    from brains.acceptance_brain import FixerWorkItem
    return FixerWorkItem


# --------------------------------------------------------------------------- #
# 1. Single FixRequest generates one FixerWorkItem
# --------------------------------------------------------------------------- #
def test_single_fix_request_generates_one_work_item():
    FixerWorkItem = _FixerWorkItem()
    requests = [FixRequest("Dashboard missing export button")]
    assert AcceptanceBrain().to_fixer_work_items(requests) == [
        FixerWorkItem("Dashboard missing export button")
    ]


# --------------------------------------------------------------------------- #
# 2. Multiple FixRequests generate multiple FixerWorkItems
# --------------------------------------------------------------------------- #
def test_multiple_fix_requests_generate_multiple_work_items():
    FixerWorkItem = _FixerWorkItem()
    requests = [
        FixRequest("Dashboard missing export button"),
        FixRequest("CSV export not visible"),
    ]
    assert AcceptanceBrain().to_fixer_work_items(requests) == [
        FixerWorkItem("Dashboard missing export button"),
        FixerWorkItem("CSV export not visible"),
    ]


# --------------------------------------------------------------------------- #
# 3. Empty FixRequest list generates no work items
# --------------------------------------------------------------------------- #
def test_empty_fix_request_list_generates_no_work_items():
    assert AcceptanceBrain().to_fixer_work_items([]) == []


# --------------------------------------------------------------------------- #
# 4. AcceptanceBrain exposes a handoff method returning list[FixerWorkItem]
# --------------------------------------------------------------------------- #
def test_to_fixer_work_items_returns_list_of_work_item():
    FixerWorkItem = _FixerWorkItem()
    items = AcceptanceBrain().to_fixer_work_items(
        [FixRequest("Dashboard missing export button")]
    )
    assert isinstance(items, list)
    assert all(isinstance(i, FixerWorkItem) for i in items)

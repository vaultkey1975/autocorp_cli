#!/usr/bin/env python3
"""
Retry controller tests  (AutoCorp CLI - Phase 8M RED)
=====================================================

Drives the design of Phase 8M: a small, side-effect-free RetryController that
tracks how many fix attempts have been made against a configured maximum. It is
PURE bookkeeping - it runs nothing, retries nothing, and wires into no
orchestrator, Builder, or Fixer. A future, separately-approved phase decides what
to do when the budget is exhausted.

Expected API (pinned by these tests, lives in `brains/retry_controller.py`):
  * RetryState dataclass: attempts (int), max_attempts (int), exhausted (bool).
  * RetryController.create(max_attempts) -> RetryState   (attempts=0, not exhausted)
  * RetryController.record_attempt(state)                (MUTATES state in place:
        attempts += 1; exhausted = attempts >= max_attempts)
  * RetryController.is_exhausted(state) -> bool          (attempts >= max_attempts)

These tests are RED on purpose and fail for MISSING IMPLEMENTATION ONLY: the
module `brains.retry_controller` does not exist yet, so RetryController and
RetryState are lazily imported inside helpers and each test fails individually
with ModuleNotFoundError rather than collapsing into one collection error.

Fully offline: no model, no network. No production code is added in this phase.
"""

import pytest


def _RetryController():
    """Lazy import: RED until GREEN adds brains/retry_controller.py."""
    from brains.retry_controller import RetryController
    return RetryController


def _RetryState():
    """Lazy import: RED until GREEN adds brains/retry_controller.py."""
    from brains.retry_controller import RetryState
    return RetryState


# --------------------------------------------------------------------------- #
# 1. Initial state: 0 attempts, configured max, not exhausted
# --------------------------------------------------------------------------- #
def test_retry_controller_initial_state():
    RetryController = _RetryController()
    RetryState = _RetryState()
    state = RetryController().create(3)
    assert isinstance(state, RetryState)
    assert state.attempts == 0
    assert state.max_attempts == 3
    assert state.exhausted is False


# --------------------------------------------------------------------------- #
# 2. First attempt: count is 1, not yet exhausted
# --------------------------------------------------------------------------- #
def test_retry_controller_first_attempt():
    RetryController = _RetryController()
    ctrl = RetryController()
    state = ctrl.create(3)
    ctrl.record_attempt(state)
    assert state.attempts == 1
    assert state.exhausted is False


# --------------------------------------------------------------------------- #
# 3. Repeated attempts increment the count
# --------------------------------------------------------------------------- #
def test_retry_controller_increments_attempt_count():
    RetryController = _RetryController()
    ctrl = RetryController()
    state = ctrl.create(5)
    ctrl.record_attempt(state)
    ctrl.record_attempt(state)
    ctrl.record_attempt(state)
    assert state.attempts == 3


# --------------------------------------------------------------------------- #
# 4. The configured maximum is respected
# --------------------------------------------------------------------------- #
def test_retry_controller_respects_max_attempts():
    RetryController = _RetryController()
    ctrl = RetryController()
    state = ctrl.create(2)
    ctrl.record_attempt(state)
    assert ctrl.is_exhausted(state) is False
    ctrl.record_attempt(state)
    assert ctrl.is_exhausted(state) is True


# --------------------------------------------------------------------------- #
# 5. Exhaustion is reported via both is_exhausted() and the state flag
# --------------------------------------------------------------------------- #
def test_retry_controller_reports_exhausted():
    RetryController = _RetryController()
    ctrl = RetryController()
    state = ctrl.create(1)
    assert ctrl.is_exhausted(state) is False
    ctrl.record_attempt(state)
    assert state.exhausted is True
    assert ctrl.is_exhausted(state) is True

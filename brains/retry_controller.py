#!/usr/bin/env python3
"""
Retry Controller  (AutoCorp CLI - brains)  [Phase 8M]
=====================================================

Pure attempt-budget bookkeeping. A RetryController tracks how many fix attempts
have been made (RetryState) against a configured maximum and reports when the
budget is exhausted.

BOOKKEEPING ONLY: it runs nothing, retries nothing, drives no loop, and wires
into no orchestrator, Builder, Fixer, or FixerExecutor. It is side-effect free
apart from mutating the RetryState it is handed. Deciding what to DO when the
budget is exhausted is a deliberate, separately-approved future phase.
"""

from dataclasses import dataclass


@dataclass
class RetryState:
    """The attempt budget for one repair sequence.

    Pure data. `attempts` is how many have been recorded, `max_attempts` the
    configured ceiling, and `exhausted` whether the ceiling has been reached."""
    attempts: int
    max_attempts: int
    exhausted: bool


class RetryController:
    """Creates and advances RetryState. Holds no state of its own."""

    def create(self, max_attempts) -> RetryState:
        """Return a fresh RetryState: zero attempts, the given ceiling, not
        exhausted."""
        return RetryState(attempts=0, max_attempts=max_attempts, exhausted=False)

    def record_attempt(self, state: RetryState) -> RetryState:
        """Record one attempt: increment the count and recompute exhaustion.
        Mutates `state` in place and returns it for convenience."""
        state.attempts += 1
        state.exhausted = state.attempts >= state.max_attempts
        return state

    def is_exhausted(self, state: RetryState) -> bool:
        """Whether the attempt budget has been reached."""
        return state.exhausted

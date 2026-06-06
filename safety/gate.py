#!/usr/bin/env python3
"""
Command Gate  (AutoCorp CLI - safety)
=====================================

The policy layer that decides whether a file write or shell command is allowed.
Every action the agent takes is routed here BEFORE it happens, which gives us a
single, well-defined seam for safety.

This is also the **Agent Watchdog integration point (FUTURE)**. Today we ship two
gates:

    * AllowAllGate  - no questions asked (used by `--auto` and tests)
    * ConfirmGate   - asks the human before each action, with "yes to all"

Later, a `WatchdogGate(CommandGate)` will implement the same two methods by
calling Agent Watchdog's deterministic rules + LLM review to APPROVE / BLOCK /
ASK. Because the orchestrator and Executor only ever talk to the `CommandGate`
interface, dropping Watchdog in is a one-line change at startup:

    session = Session(gate=WatchdogGate())   # instead of ConfirmGate()

Nothing in the brains or executor changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core import console


@dataclass
class Decision:
    """A gate's verdict on a proposed action.

    `risk_score` (0-10) is optional: gates that don't compute one leave it None.
    WatchdogGate fills it in from Agent Watchdog's review.
    """
    action: str            # "allow" | "block"
    reason: str = ""
    risk_score: int = None

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @classmethod
    def allow(cls, reason: str = "", risk_score: int = None) -> "Decision":
        return cls("allow", reason, risk_score)

    @classmethod
    def block(cls, reason: str = "", risk_score: int = None) -> "Decision":
        return cls("block", reason, risk_score)


class CommandGate(ABC):
    """Interface every gate (and the future WatchdogGate) implements."""

    @abstractmethod
    def review_write(self, path: str, content: str) -> Decision:
        """Decide whether writing `content` to `path` is allowed."""

    @abstractmethod
    def review_command(self, command: str, cwd: str) -> Decision:
        """Decide whether running `command` in `cwd` is allowed."""


class AllowAllGate(CommandGate):
    """Baseline gate: permit everything. Used by `--auto` and non-interactive runs."""

    def review_write(self, path: str, content: str) -> Decision:
        return Decision.allow("auto")

    def review_command(self, command: str, cwd: str) -> Decision:
        return Decision.allow("auto")


class ConfirmGate(CommandGate):
    """
    Interactive gate: ask the human before each write/command. Choosing the
    "all" option flips a sticky flag so the rest of the run proceeds without
    further prompts.
    """

    def __init__(self):
        self._allow_all = False

    def _ask(self, what: str) -> Decision:
        if self._allow_all:
            return Decision.allow("yes-to-all")
        # rich Confirm only does yes/no, so offer the sticky option explicitly.
        console.muted("   [a]=yes to all for the rest of this run")
        answer = console.ask(f"Allow {what}? [y/n/a]", default="y").strip().lower()
        if answer in ("a", "all"):
            self._allow_all = True
            return Decision.allow("yes-to-all")
        if answer in ("", "y", "yes"):
            return Decision.allow("approved")
        return Decision.block("declined by user")

    def review_write(self, path: str, content: str) -> Decision:
        return self._ask(f"write [cyan]{path}[/cyan] ({len(content)} bytes)")

    def review_command(self, command: str, cwd: str) -> Decision:
        return self._ask(f"run [bold]{command}[/bold]")

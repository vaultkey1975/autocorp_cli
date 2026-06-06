#!/usr/bin/env python3
"""
Executor  (AutoCorp CLI - safety)
=================================

The ONLY code in AutoCorp that touches the filesystem or runs a shell command.
Brains describe *what* they want done and hand it to the Executor; the Executor
asks the `CommandGate` for permission, and only then acts.

Centralising this means:
  * one place enforces safety (and, later, the Agent Watchdog gate),
  * brains stay pure (no `os`/`subprocess` scattered around),
  * every action is uniformly logged and reportable.
"""

import os
import subprocess
from dataclasses import dataclass, field

from config import COMMAND_TIMEOUT
from core import console
from safety.gate import CommandGate


@dataclass
class WriteResult:
    path: str
    written: bool
    blocked: bool = False
    reason: str = ""


@dataclass
class CommandResult:
    command: str
    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    blocked: bool = False
    reason: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return (not self.blocked) and (not self.timed_out) and self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()


class Executor:
    """Performs gated file writes and command runs."""

    def __init__(self, gate: CommandGate):
        self.gate = gate

    @staticmethod
    def _show_block(label: str, target: str, decision, tail: str) -> None:
        """Render the prominent red 'BLOCKED' panel used for both writes and
        commands. The action is never performed when this is shown."""
        risk = f"  risk {decision.risk_score}/10" if decision.risk_score is not None else ""
        console.show_panel(
            f"⛔ {label} BLOCKED{risk}",
            f"{target}\n\n{decision.reason}\n\n{tail}",
            style="red",
        )

    # ----- filesystem ----- #
    def write_file(self, path: str, content: str) -> WriteResult:
        decision = self.gate.review_write(path, content)
        if not decision.allowed:
            self._show_block("Write", path, decision, "(not written)")
            return WriteResult(path, written=False, blocked=True, reason=decision.reason)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
        except OSError as e:
            console.error(f"Write failed: {path} ({e})")
            return WriteResult(path, written=False, reason=str(e))
        console.success(f"Wrote {path}")
        return WriteResult(path, written=True)

    # ----- shell ----- #
    def run_command(self, command: str, cwd: str) -> CommandResult:
        decision = self.gate.review_command(command, cwd)
        if not decision.allowed:
            # Make the block prominent in the pipeline - the command is NOT run.
            self._show_block("Command", f"$ {command}", decision, "(not executed)")
            return CommandResult(command, blocked=True, reason=decision.reason)
        # Make a scored review (e.g. WatchdogGate) visible in the pipeline.
        if decision.risk_score is not None:
            console.muted(f"   gate: approved (risk {decision.risk_score}/10) — {decision.reason}")
        try:
            proc = subprocess.run(
                command, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
            )
        except subprocess.TimeoutExpired as e:
            console.error(f"Command timed out after {COMMAND_TIMEOUT}s: {command}")
            return CommandResult(command, timed_out=True,
                                 stdout=e.stdout or "", stderr=e.stderr or "")
        except OSError as e:
            console.error(f"Command failed to start: {command} ({e})")
            return CommandResult(command, returncode=-1, stderr=str(e))
        return CommandResult(command, returncode=proc.returncode,
                             stdout=proc.stdout, stderr=proc.stderr)

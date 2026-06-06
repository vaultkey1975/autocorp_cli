#!/usr/bin/env python3
"""
WatchdogGate  (AutoCorp CLI - safety)
=====================================

An OPTIONAL safety gate that defers command approval to the **separate** Agent
Watchdog app (`~/agent_watchdog_brain`).

Important design points:
  * The two apps stay separate. Agent Watchdog is loaded at runtime *as a
    library* from a configurable path - none of its code is copied or merged in.
  * If Agent Watchdog cannot be loaded (not installed, import error, wrong path),
    this gate falls back safely to the interactive `ConfirmGate`.
  * This gate only *reviews* command strings - it never executes anything.

Command review (when Agent Watchdog is available):
  1. Deterministic, offline pattern rules (`command_rules`) - an instant BLOCK on
     a known-dangerous command, no AI needed, cannot be overridden.
  2. Optional llama3.2 risk scoring (`watchdog_brain.review_action`) for the rest:
     block when risk >= WATCHDOG_BLOCK_THRESHOLD or the recommendation is BLOCK.

File writes are auto-approved (risk 0): AutoCorp only writes sanitized relative
paths into its own workspace, so writes are sandboxed. WatchdogGate focuses on
commands, which is Agent Watchdog's domain.
"""

import os
import sys

from config import WATCHDOG_BLOCK_THRESHOLD, WATCHDOG_PATH, WATCHDOG_USE_AI
from core import console
from safety.gate import CommandGate, ConfirmGate, Decision


class WatchdogGate(CommandGate):
    """Routes command approval through Agent Watchdog, with a safe fallback."""

    def __init__(self, fallback: CommandGate = None, watchdog_path: str = WATCHDOG_PATH):
        self.watchdog_path = watchdog_path
        self.use_ai = WATCHDOG_USE_AI
        self.available = False
        self._detect = None         # command_rules.detect_dangerous_patterns
        self._review = None         # watchdog_brain.review_action
        # The gate we defer to when Agent Watchdog can't help.
        self._fallback = fallback or ConfirmGate()
        self._load()

    # ------------------------------------------------------------------ #
    # Optional runtime load of the separate Agent Watchdog app
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        path = self.watchdog_path
        if not path or not os.path.isdir(path):
            console.warn(
                f"Agent Watchdog not found at {path or '(unset)'}; "
                "WatchdogGate will fall back to confirm prompts."
            )
            return
        # Append (not insert) so Watchdog's modules can never shadow AutoCorp's.
        if path not in sys.path:
            sys.path.append(path)
        try:
            import command_rules  # deterministic rules (offline)
            self._detect = command_rules.detect_dangerous_patterns
            if self.use_ai:
                import watchdog_brain  # llama3.2 risk review
                self._review = watchdog_brain.review_action
            self.available = True
            mode = "rules + AI" if self.use_ai else "rules only"
            console.success(f"WatchdogGate active ({mode}) via {path}")
        except Exception as e:  # noqa: BLE001 - any load failure -> safe fallback
            self.available = False
            console.warn(
                f"Could not load Agent Watchdog ({e}); "
                "WatchdogGate will fall back to confirm prompts."
            )

    # ------------------------------------------------------------------ #
    # CommandGate interface
    # ------------------------------------------------------------------ #
    def review_command(self, command: str, cwd: str) -> Decision:
        if not self.available:
            return self._fallback.review_command(command, cwd)

        # 1) Deterministic block (offline, authoritative).
        try:
            hits = self._detect(command) or []
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            labels = ", ".join(label for label, _ in hits)
            why = "; ".join(f"{label}: {reason}" for label, reason in hits)
            return Decision.block(
                reason=f"risk 10/10 — dangerous pattern: {labels}. {why}",
                risk_score=10,
            )

        # 2) Optional AI risk scoring for everything else.
        if self._review is not None:
            try:
                result = self._review(
                    command,
                    task_context="Vet this shell command for safety before execution.",
                )
            except Exception as e:  # noqa: BLE001 - Watchdog/Ollama trouble -> safe fallback
                console.warn(f"Watchdog AI review failed ({e}); asking for confirmation.")
                return self._fallback.review_command(command, cwd)

            score = int(result.get("risk_score", 0) or 0)
            rec = result.get("recommended_action", "") or ""
            blocked = score >= WATCHDOG_BLOCK_THRESHOLD or rec.upper().startswith("BLOCK")
            reason = f"risk {score}/10 — {rec or 'reviewed by Agent Watchdog'}"
            return (Decision.block if blocked else Decision.allow)(
                reason=reason, risk_score=score
            )

        # 3) Rules-only mode, no pattern match -> allow.
        return Decision.allow(reason="no dangerous pattern matched", risk_score=0)

    def review_write(self, path: str, content: str) -> Decision:
        # AutoCorp writes only sanitized relative paths into its own workspace.
        return Decision.allow(reason="file write (sandboxed workspace)", risk_score=0)

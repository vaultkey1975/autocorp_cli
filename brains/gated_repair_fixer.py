#!/usr/bin/env python3
"""
Gated Repair Fixer  (AutoCorp CLI - brains)

A thin adapter that lets the self-healing loop drive real, gated, write-only
repairs. Every write goes through executor.write_file.

Workspace-aware behavior:
- If workspace is provided and an action path is relative, anchor it inside that
  workspace before writing.
- If workspace is not provided, preserve legacy relative write behavior.
"""

import os

from brains.fixer_executor import FixerExecutor


class GatedRepairFixer:
    """Drives propose() -> apply() through an injected gated Executor."""

    def __init__(self, executor, generator=None, workspace=None):
        self.executor = executor
        self.generator = generator
        self.workspace = workspace
        self._fixer = FixerExecutor()

    def _anchor_path(self, path):
        """Return a workspace-anchored path when configured.

        Legacy behavior is preserved when workspace is None.
        """
        if not path:
            return path
        if self.workspace is None:
            return path
        if os.path.isabs(path):
            return path
        return os.path.join(self.workspace, path)

    def execute(self, work_items) -> list:
        """Propose repairs, optionally generate content, anchor paths, and apply."""
        actions = self._fixer.propose(work_items)

        for index, action in enumerate(actions):
            if not action.path:
                action.path = f"repairs/repair_{index}.txt"

            if self.generator is not None:
                generated = self.generator.generate(action.path, action.content)
                if generated:
                    action.content = generated

            action.path = self._anchor_path(action.path)

        return self._fixer.apply(actions, self.executor)

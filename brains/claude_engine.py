#!/usr/bin/env python3
"""
Claude Engine  (AutoCorp CLI - brains)  [Claude CLI Integration Phase 1]
=======================================================================

An optional engine that generates code by shelling out to the Claude CLI in
non-interactive "print" mode:

    claude -p  [--append-system-prompt SYSTEM]      (prompt piped on stdin)

It captures stdout as the generated output. Every failure mode - the CLI not
being installed, a non-zero exit, a timeout, or empty output - is turned into a
clean EngineError with a useful message. It never raises a raw exception and
never crashes the build; the Builder catches EngineError and continues.
"""

import shutil
import subprocess

from brains.base_engine import BaseEngine, EngineError


class ClaudeEngine(BaseEngine):
    name = "claude"

    def __init__(self, command: str = "claude", timeout: int = 180):
        self.command = command
        self.timeout = timeout

    def available(self) -> bool:
        """True if the Claude CLI binary is on PATH."""
        return shutil.which(self.command) is not None

    def generate(self, prompt: str, system: str = "") -> str:
        if not self.available():
            raise EngineError(
                f"Claude CLI ('{self.command}') was not found on PATH. "
                "Install Claude Code or use --engine local."
            )

        args = [self.command, "-p"]
        if system:
            # Keep the Builder's per-file system instruction without letting the
            # CLI run any tools - we only want text generation.
            args += ["--append-system-prompt", system]

        try:
            proc = subprocess.run(
                args,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise EngineError(
                f"Claude CLI timed out after {self.timeout}s."
            ) from e
        except OSError as e:
            raise EngineError(f"Could not run Claude CLI: {e}") from e

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise EngineError(
                f"Claude CLI failed (exit {proc.returncode}): "
                f"{detail[:300] or 'no error output'}"
            )

        output = (proc.stdout or "").strip()
        if not output:
            raise EngineError("Claude CLI returned no output.")
        return output

#!/usr/bin/env python3
"""Full regression runner for Reliability Engine merges."""

import shlex
import subprocess
import sys
from dataclasses import dataclass

from reliability_engine.env_isolation import clean_subprocess_env


@dataclass
class RegressionResult:
    ok: bool
    command: str
    returncode: int
    output: str


class RegressionRunner:
    def __init__(self, command: str | None = None):
        self.command = command or self.default_command()

    @staticmethod
    def default_command() -> str:
        return f"{shlex.quote(sys.executable)} -m pytest -q"

    def run(self, cwd: str) -> RegressionResult:
        proc = subprocess.run(
            self.command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            env=clean_subprocess_env(),
        )
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return RegressionResult(proc.returncode == 0, self.command, proc.returncode, output)

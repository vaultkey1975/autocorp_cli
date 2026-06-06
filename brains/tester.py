#!/usr/bin/env python3
"""
Tester Brain  (AutoCorp CLI - brains)
=====================================

Runs the project's tests through the Executor, reads any failure output, and asks
the model for a fix. The orchestrator drives the retry loop; this brain provides
the two primitives: `test()` and `suggest_fix()`.
"""

import os
import re

from core import console, llm

FIX_SYSTEM_PROMPT = """You are the Tester Brain of a local AI coding assistant.
A test run failed. You are given the failing file's current contents and the
error output. Return a corrected version of THAT file.

Respond with ONLY a single valid JSON object, no markdown, in EXACTLY this shape:

{
  "explanation": "<one sentence: what was wrong and how you fixed it>",
  "filename": "<the relative path of the file you are fixing>",
  "new_content": "<the COMPLETE corrected contents of that file>"
}

Fix the actual root cause. Keep changes minimal. Output valid code in new_content."""


class TesterBrain:
    def __init__(self, executor, model=None):
        self.executor = executor
        self.model = model or llm.MODEL

    def test(self, workspace: str, plan: dict):
        """Run the plan's test command in the workspace. Returns a CommandResult."""
        command = plan.get("test_command") or self._infer_command(workspace)
        console.info(f"Running tests: [bold]{command}[/bold]")
        result = self.executor.run_command(command, cwd=workspace)
        if result.blocked:
            console.warn("Test run was blocked by the gate.")
        elif result.ok:
            console.success("Tests passed.")
        else:
            console.error(f"Tests failed (exit {result.returncode}).")
        return result

    def _infer_command(self, workspace: str) -> str:
        """Best-effort default when the plan gave no test command."""
        for root, _dirs, files in os.walk(workspace):
            if any(f.startswith("test") and f.endswith(".py") for f in files):
                return "python -m pytest -q"
        # Fall back to running a main.py if present.
        if os.path.exists(os.path.join(workspace, "main.py")):
            return "python main.py"
        return "python -m pytest -q"

    def suggest_fix(self, workspace: str, filename: str, error_output: str,
                    plan: dict = None) -> dict:
        """Ask the model to repair `filename` given the error. Returns
        {explanation, filename, new_content} or {} if it couldn't. Sibling files
        are included as read-only context so a fix to the test matches the impl
        (and vice-versa)."""
        full_path = os.path.join(workspace, filename)
        try:
            with open(full_path, encoding="utf-8") as f:
                current = f.read()
        except OSError:
            current = ""

        context = ""
        for f in (plan or {}).get("files", []):
            sib = f.get("path") if isinstance(f, dict) else f
            if not sib or sib == filename:
                continue
            try:
                with open(os.path.join(workspace, sib), encoding="utf-8") as fh:
                    context += f"\n--- {sib} (context, do not rewrite) ---\n{fh.read()[:2000]}\n"
            except OSError:
                continue

        prompt = (
            f"FAILING FILE TO FIX: {filename}\n\n"
            f"CURRENT CONTENTS OF {filename}:\n{current}\n\n"
        )
        if context:
            prompt += f"OTHER PROJECT FILES (context only):\n{context}\n\n"
        prompt += (
            f"ERROR OUTPUT:\n{error_output[:4000]}\n\n"
            f"Fix the root cause. If the error is a wrong assertion in a test, "
            f"correct the assertion to match the real behaviour of the code above. "
            f"Return the corrected {filename} as the specified JSON."
        )
        try:
            parsed = llm.generate_json(prompt, system=FIX_SYSTEM_PROMPT, model=self.model)
        except (llm.OllamaError, ValueError) as e:
            console.error(f"Could not get a fix from the model: {e}")
            return {}

        new_content = llm.strip_code_fences(str(parsed.get("new_content", "")))
        if not new_content.strip():
            return {}
        return {
            "explanation": str(parsed.get("explanation", "")).strip(),
            "filename": filename,
            "new_content": new_content,
        }

    def pick_file_to_fix(self, plan: dict, error_output: str) -> str:
        """Choose which file the error most likely refers to.

        Prefer the deepest `file.py:line` frame in the traceback (where the
        failure actually occurred) - this correctly targets a test file with a
        bad assertion. Fall back to basename mention, then the first non-test
        source file."""
        files = [f["path"] for f in plan.get("files", [])]
        basenames = {os.path.basename(p): p for p in files}

        # Frames look like:  test_mathx.py:7: AssertionError
        frames = re.findall(r"([\w./-]+\.py):\d+", error_output)
        for frame in reversed(frames):  # deepest/last frame first
            base = os.path.basename(frame)
            if base in basenames:
                return basenames[base]

        for base, path in basenames.items():
            if base in error_output:
                return path
        for path in files:
            if not os.path.basename(path).lower().startswith("test"):
                return path
        return files[0] if files else ""

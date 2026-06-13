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
from brains.base_engine import EngineError

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
    def __init__(self, executor, model=None, engine=None):
        self.executor = executor
        self.model = model or llm.MODEL
        # Optional BaseEngine (DS5). When set, suggest_fix generates the fix
        # through the engine instead of the direct llm.generate_json call; None
        # preserves the legacy local-model path exactly.
        self.engine = engine

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

    @staticmethod
    def _render_findings(findings) -> str:
        """Render reviewer findings as an ADVISORY prompt section. Returns "" for
        falsy/empty input (so the prompt is byte-identical to before when no
        findings are supplied). Items render in stable INPUT order; each may be a
        Finding object (with attributes) or a dict; anything unreadable is safely
        skipped. Never raises."""
        if not findings:
            return ""
        lines = []
        for item in findings:
            try:
                if isinstance(item, dict):
                    get = item.get
                elif hasattr(item, "message") or hasattr(item, "category"):
                    get = lambda k, d=None, _it=item: getattr(_it, k, d)
                else:
                    continue
                message = get("message", "") or ""
                if not str(message).strip():
                    continue
                category = get("category", "") or ""
                path = get("file", "") or ""
                line = get("line", "") or ""
                where = f"{path}:{line}" if path else ""
                tag = f"[{category}] " if category else ""
                prefix = f"{where} " if where else ""
                lines.append(f"  - {prefix}{tag}{message}".rstrip())
            except Exception:  # noqa: BLE001 - a bad item must never break fixing
                continue
        if not lines:
            return ""
        return (
            "CODE REVIEW FINDINGS (advisory — use only if relevant to the fix):\n"
            + "\n".join(lines) + "\n\n"
        )

    def suggest_fix(self, workspace: str, filename: str, error_output: str,
                    plan: dict = None, findings=None) -> dict:
        """Ask the model to repair `filename` given the error. Returns
        {explanation, filename, new_content} or {} if it couldn't. Sibling files
        are included as read-only context so a fix to the test matches the impl
        (and vice-versa).

        `findings` (optional) are reviewer findings folded in as ADVISORY context;
        when None or empty the prompt is unchanged."""
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
        review_section = self._render_findings(findings)
        if review_section:
            prompt += review_section
        prompt += (
            f"ERROR OUTPUT:\n{error_output[:4000]}\n\n"
            f"Fix the root cause. If the error is a wrong assertion in a test, "
            f"correct the assertion to match the real behaviour of the code above. "
            f"Return the corrected {filename} as the specified JSON."
        )
        if self.engine is not None:
            try:
                raw = self.engine.generate(prompt, system=FIX_SYSTEM_PROMPT)
                parsed = llm.extract_json(raw)
            except (EngineError, ValueError) as e:
                console.error(f"Could not get a fix from the engine: {e}")
                return {}
        else:
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

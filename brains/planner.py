#!/usr/bin/env python3
"""
Planner Brain  (AutoCorp CLI - brains)  [v2 + Template Selection]
================================================================

Turns a free-text request into a concrete, validated, *structured* ProjectPlan
BEFORE any code is written.

Two planning paths:
  1. TEMPLATE path - if the request matches a known project template (e.g. a
     PySide6 desktop app: "build a desktop calculator", "a GUI todo app", ...),
     the plan's *shape* (files, purposes, dependency-safe build_order, test
     command) comes deterministically from the template, so the structure is
     always correct. Code is still generated per-file by the Builder/engine.
  2. LLM path - for every other request, the model produces the structured plan
     exactly as before. This path is unchanged, so non-GUI projects have no
     regressions.

Both paths funnel through `_finalize`: validate required fields, save to memory,
and return a plain dict carrying the full v2 structure plus the v1 keys, so all
existing consumers (orchestrator, builder, tester) keep working.
"""

from core import console, llm
from brains.project_plan import (
    ProjectPlan,
    REQUIRED_FIELDS,
    sanitize_name,
    safe_relpath,
)
from brains.templates import select_template
from memory import store

# Re-export sanitisers under their historical names for backward compatibility
# with any code/tests that imported them from this module.
_sanitize_name = sanitize_name
_safe_relpath = safe_relpath


SYSTEM_PROMPT = """You are the Planner Brain of a local AI coding assistant.
Given a user's request, produce a concrete, structured build plan for a small,
self-contained project. Think in terms of the minimum files needed to satisfy the
request, and always include at least one automated test.

Respond with ONLY a single valid JSON object, no markdown, in EXACTLY this shape:

{
  "project_name": "<short snake_case name, no spaces>",
  "project_type": "<one of: cli | library | script | api | web>",
  "language": "<primary language, e.g. python>",
  "summary": "<one or two sentences describing what will be built>",
  "files": [
    {"path": "<relative/path.ext>", "purpose": "<what this file is for>"}
  ],
  "build_order": ["<relative/path.ext in the order it should be created>", "..."],
  "test_command": "<shell command to run the tests, e.g. python -m pytest -q>",
  "success_criteria": ["<a checkable condition that means the project is done>", "..."]
}

Rules:
- Keep it minimal and runnable. Prefer the standard library; avoid heavy deps.
- Use relative paths only (no leading / and no '..').
- build_order must list exactly the paths in "files"; put implementation files
  before their test files.
- For Python, include a pytest-style test file and a pytest test_command.
- success_criteria should be concrete (e.g. "pytest reports all tests passing").
- project_name must be a valid directory name."""


def normalize_plan(plan: dict, request: str = "") -> dict:
    """Backward-compatible helper: normalise raw data into a plain plan dict.

    Delegates to ProjectPlan so there is a single normalisation path."""
    return ProjectPlan.from_dict(plan, request).to_dict()


class PlannerBrain:
    def __init__(self, model=None):
        self.model = model or llm.MODEL

    def plan(self, request: str, lessons_text: str = "") -> dict:
        """Generate, validate, persist, and return a structured plan (as a dict).

        Uses a matching project template when one applies; otherwise asks the
        model. Raises llm.OllamaError only on the LLM path's transport failure
        (the orchestrator handles that)."""
        template = select_template(request)
        if template is not None:
            console.muted(f"Planner: using template '{template.NAME}'.")
            raw = template.build_plan(request)
            return self._finalize(ProjectPlan.from_dict(raw, request), request)

        # ----- LLM path (unchanged) -----
        prompt = f"USER REQUEST:\n{request}\n"
        if lessons_text:
            prompt += f"\n{lessons_text}\n"
        prompt += "\nProduce the structured build plan JSON now."

        parsed = llm.generate_json(prompt, system=SYSTEM_PROMPT, model=self.model)
        return self._finalize(ProjectPlan.from_dict(parsed, request), request)

    def _finalize(self, project_plan: "ProjectPlan", request: str) -> dict:
        """Validate (non-fatal), persist to memory, and return the plan dict.
        Shared by both the template and LLM planning paths."""
        errors = project_plan.validate()
        if errors:
            console.warn("Plan validation issues: " + "; ".join(errors))

        plan = project_plan.to_dict()

        # Requirement (Planner v2): save generated plans into the memory system.
        try:
            store.save_plan(plan, request=request)
        except Exception:  # noqa: BLE001 - persistence must never break planning
            pass

        return plan

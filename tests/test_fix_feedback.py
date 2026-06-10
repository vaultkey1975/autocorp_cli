#!/usr/bin/env python3
"""
Reviewer -> Fix Loop feedback: fixer unit tests  (AutoCorp CLI - Phase 8D RED)
=============================================================================

Drives the contract change on `TesterBrain.suggest_fix`: it gains an OPTIONAL,
advisory `findings` channel so the fixer can see the Reviewer's findings while
repairing a failing build.

Guarantees pinned here:
  * findings are folded into the fix prompt (advisory context),
  * `findings=None` (or absent) is byte-identical to today's behaviour,
  * empty findings behave like no findings,
  * malformed findings never crash the fixer,
  * rendering is deterministic and order-preserving.

RED: `suggest_fix` does not accept a `findings` argument yet, so every call that
passes it raises `TypeError`. Fully offline: `core.llm.generate_json` is patched
to capture the prompt and return a canned fix - Ollama is never contacted.
"""

from unittest.mock import patch

import pytest

from brains.reviewer import Finding
from brains.tester import TesterBrain
from safety.executor import Executor
from safety.gate import AllowAllGate

_SENTINEL = object()


def _capture_fix_prompt(tmp_path, findings=_SENTINEL, error="ERR",
                        filename="main.py", file_content="x = 1\n"):
    """Run suggest_fix with llm.generate_json patched; return (prompt, result).

    When `findings` is the sentinel, suggest_fix is called WITHOUT the findings
    argument (today's call shape)."""
    (tmp_path / filename).write_text(file_content)
    plan = {"files": [{"path": filename, "purpose": "p"}]}
    tester = TesterBrain(Executor(AllowAllGate()))
    captured = {}

    def fake_generate_json(prompt, system="", model=None):
        captured["prompt"] = prompt
        return {"explanation": "fixed", "filename": filename, "new_content": "x = 2\n"}

    with patch("core.llm.generate_json", fake_generate_json):
        if findings is _SENTINEL:
            result = tester.suggest_fix(str(tmp_path), filename, error, plan)
        else:
            result = tester.suggest_fix(str(tmp_path), filename, error, plan,
                                        findings=findings)
    return captured.get("prompt", ""), result


def _finding(message, line=1, category="missing_import", severity="error"):
    return Finding(file="main.py", line=line, severity=severity,
                   category=category, symbol="s", message=message)


# --------------------------------------------------------------------------- #
# A. Fix Loop integration (fixer side)
# --------------------------------------------------------------------------- #
def test_findings_passed_into_fix_prompt(tmp_path):
    prompt, result = _capture_fix_prompt(
        tmp_path, findings=[_finding("UNIQUE_FINDING_MESSAGE_42")])
    assert "UNIQUE_FINDING_MESSAGE_42" in prompt
    assert "review finding" in prompt.lower()
    assert result  # a fix is still produced


def test_empty_findings_handled_safely(tmp_path):
    prompt, result = _capture_fix_prompt(tmp_path, findings=[])
    assert result
    # Empty findings behave exactly like "no findings": no advisory section.
    assert "review finding" not in prompt.lower()


def test_multiple_findings_preserved_in_order(tmp_path):
    prompt, _ = _capture_fix_prompt(tmp_path, findings=[
        _finding("MSG_ALPHA"), _finding("MSG_BETA"), _finding("MSG_GAMMA")])
    assert "MSG_ALPHA" in prompt and "MSG_BETA" in prompt and "MSG_GAMMA" in prompt
    assert prompt.index("MSG_ALPHA") < prompt.index("MSG_BETA") < prompt.index("MSG_GAMMA")


# --------------------------------------------------------------------------- #
# B. Backward compatibility
# --------------------------------------------------------------------------- #
def test_no_findings_param_matches_findings_none(tmp_path):
    prompt_default, _ = _capture_fix_prompt(tmp_path)               # today's call
    prompt_none, _ = _capture_fix_prompt(tmp_path, findings=None)   # new None path
    assert prompt_none == prompt_default


def test_findings_none_has_no_review_section(tmp_path):
    prompt, _ = _capture_fix_prompt(tmp_path, findings=None)
    assert "review finding" not in prompt.lower()


# --------------------------------------------------------------------------- #
# C. Non-blocking guarantees
# --------------------------------------------------------------------------- #
def test_malformed_findings_do_not_crash(tmp_path):
    prompt, result = _capture_fix_prompt(
        tmp_path, findings=["just a string", {"message": "dict finding"}, None, 123])
    assert result  # fixer still returns a fix; no exception raised


def test_findings_without_expected_attrs_are_safe(tmp_path):
    class _Bare:
        pass

    _, result = _capture_fix_prompt(tmp_path, findings=[_Bare()])
    assert result


# --------------------------------------------------------------------------- #
# D. Determinism
# --------------------------------------------------------------------------- #
def test_findings_prompt_is_deterministic(tmp_path):
    findings = [_finding("DET_ONE"), _finding("DET_TWO")]
    p1, _ = _capture_fix_prompt(tmp_path, findings=findings)
    p2, _ = _capture_fix_prompt(tmp_path, findings=findings)
    assert p1 == p2


def test_findings_rendered_in_stable_input_order(tmp_path):
    prompt, _ = _capture_fix_prompt(
        tmp_path, findings=[_finding("ORDER_FIRST"), _finding("ORDER_SECOND")])
    assert prompt.index("ORDER_FIRST") < prompt.index("ORDER_SECOND")

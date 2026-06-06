"""Tests for the safety gates (safety/gate).

The Executor and orchestrator only ever talk to the CommandGate interface, so
these gates are the policy seam. ConfirmGate's human prompt is driven through
core.console.ask, which we monkeypatch to simulate answers.
"""

from core import console
from safety.gate import AllowAllGate, ConfirmGate, Decision


def test_decision_helpers():
    allow = Decision.allow("ok")
    block = Decision.block("nope", risk_score=9)
    assert allow.allowed is True
    assert block.allowed is False
    assert block.risk_score == 9
    assert allow.risk_score is None


def test_allow_all_gate_permits_everything():
    gate = AllowAllGate()
    assert gate.review_write("any/path.py", "data").allowed
    assert gate.review_command("rm -rf /", "/tmp").allowed


def test_confirm_gate_yes(monkeypatch):
    monkeypatch.setattr(console, "ask", lambda *a, **k: "y")
    gate = ConfirmGate()
    assert gate.review_write("f.py", "x").allowed
    assert gate.review_command("ls", "/tmp").allowed


def test_confirm_gate_no(monkeypatch):
    monkeypatch.setattr(console, "ask", lambda *a, **k: "n")
    gate = ConfirmGate()
    decision = gate.review_command("rm file", "/tmp")
    assert decision.allowed is False
    assert "declined" in decision.reason.lower()


def test_confirm_gate_yes_to_all_is_sticky(monkeypatch):
    gate = ConfirmGate()
    # First answer "a" (yes-to-all) flips the sticky flag.
    monkeypatch.setattr(console, "ask", lambda *a, **k: "a")
    assert gate.review_command("first", "/tmp").allowed
    # Now even a "no" answer is never consulted — subsequent actions auto-allow.
    monkeypatch.setattr(console, "ask", lambda *a, **k: "n")
    assert gate.review_write("later.py", "x").allowed

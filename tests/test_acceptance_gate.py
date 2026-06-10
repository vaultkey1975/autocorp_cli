#!/usr/bin/env python3
"""
Acceptance Gate: evaluate + report  (AutoCorp CLI - Phase 8F RED)
================================================================

Drives `AcceptanceGate.evaluate(criteria, context)` and the `AcceptanceReport`
schema. Criteria are the human-readable strings from a TEAM_PROFILE; the gate
maps each to a deterministic check, runs it, and reports pass/fail/unverified.
Unknown criteria are `unverified` (never block); a check that raises is caught
(non-blocking). RED: `brains.acceptance` does not exist yet.

Fully offline: no engine, no model, no network.
"""

import pytest


def _ctx(tmp_path, test_passed=True, review_findings=None):
    from brains.acceptance import AcceptanceContext
    return AcceptanceContext(
        workspace=str(tmp_path), plan={}, request="req",
        test_passed=test_passed, review_findings=review_findings or [],
    )


def _gate():
    from brains.acceptance import AcceptanceGate
    return AcceptanceGate()


TESTS_PASS = "pytest reports all tests passing"
UNKNOWN = "the build should feel delightful"


# --------------------------------------------------------------------------- #
# Report type + schema
# --------------------------------------------------------------------------- #
def test_evaluate_returns_acceptance_report(tmp_path):
    from brains.acceptance import AcceptanceReport
    report = _gate().evaluate([TESTS_PASS], _ctx(tmp_path))
    assert isinstance(report, AcceptanceReport)


def test_report_schema(tmp_path):
    d = _gate().evaluate([TESTS_PASS], _ctx(tmp_path)).to_dict()
    for key in ("accepted", "total", "passed", "failed", "unverified",
                "results", "summary"):
        assert key in d
    assert isinstance(d["results"], list)
    for r in d["results"]:
        for key in ("criterion", "check", "status", "detail"):
            assert key in r


# --------------------------------------------------------------------------- #
# evaluate behaviour
# --------------------------------------------------------------------------- #
def test_all_matched_checks_pass_means_accepted(tmp_path):
    report = _gate().evaluate([TESTS_PASS], _ctx(tmp_path, test_passed=True))
    assert report.accepted is True


def test_a_failing_check_means_not_accepted(tmp_path):
    report = _gate().evaluate([TESTS_PASS], _ctx(tmp_path, test_passed=False))
    assert report.accepted is False


def test_unknown_criterion_is_unverified_and_does_not_block(tmp_path):
    report = _gate().evaluate([UNKNOWN], _ctx(tmp_path, test_passed=True))
    assert report.results[0]["status"] == "unverified"
    assert report.accepted is True  # unverified never blocks


def test_input_order_preserved(tmp_path):
    report = _gate().evaluate([TESTS_PASS, UNKNOWN], _ctx(tmp_path))
    assert report.results[0]["criterion"] == TESTS_PASS
    assert report.results[1]["criterion"] == UNKNOWN


def test_evaluate_is_deterministic(tmp_path):
    ctx = _ctx(tmp_path, test_passed=True)
    gate = _gate()
    assert gate.evaluate([TESTS_PASS, UNKNOWN], ctx).to_dict() == \
        gate.evaluate([TESTS_PASS, UNKNOWN], ctx).to_dict()


# --------------------------------------------------------------------------- #
# Non-blocking: a check that raises must not crash evaluate
# --------------------------------------------------------------------------- #
def test_check_exception_is_non_blocking(tmp_path, monkeypatch):
    import brains.acceptance as acc

    def boom(ctx):
        raise RuntimeError("check exploded")

    monkeypatch.setitem(acc.CHECKS, "tests_pass", boom)
    report = _gate().evaluate([TESTS_PASS], _ctx(tmp_path))  # must not raise
    assert report.results[0]["status"] != "pass"

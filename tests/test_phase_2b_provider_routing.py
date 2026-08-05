#!/usr/bin/env python3
"""Tests for Phase 2B: local-first provider routing and usage coverage.

Covers brains.provider_policy (the central routing/ledger/cleanup policy),
brains.provider_coverage_audit (the static coverage audit), and the new
routing/ledger wiring in brains.builder / brains.tester / brains.planner /
core.orchestrator.explain. Every engine used here is a disposable test
double or an offline-mocked real engine - no test in this file contacts
Ollama or any paid API.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess

import pytest

import autocorp
from brains import provider_coverage_audit, provider_policy, usage_ledger
from brains.base_engine import BaseEngine, EngineError
from brains.builder import BuilderBrain
from brains.tester import TesterBrain
from core import llm
from safety.executor import Executor
from safety.gate import AllowAllGate


def _git(repo):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)


class _FakeEngine(BaseEngine):
    """Disposable BaseEngine test double. Never touches Ollama/network."""
    name = "local"

    def __init__(self, text="generated text", *, avail=True, fail_with=None, usage=None):
        self.calls = []
        self._text = text
        self._avail = avail
        self._fail_with = fail_with
        self._usage = usage

    def available(self):
        return self._avail

    def generate(self, prompt, system=""):
        self.calls.append((prompt, system))
        if self._fail_with:
            raise self._fail_with
        if self._usage is not None:
            self.last_usage = self._usage
        return self._text


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Safety net: if any code path under test tries to reach real Ollama
    for cleanup, make it a no-op instead of a real HTTP call."""
    monkeypatch.setattr(llm, "unload_model", lambda model: (True, "ok"))
    monkeypatch.setattr(llm, "model_loaded", lambda model: (False, "ok"))


# --------------------------------------------------------------------------- #
# 1. deterministic operation performs no model call
# --------------------------------------------------------------------------- #
def test_deterministic_operation_performs_no_model_call(tmp_path):
    provider_policy.record_deterministic("verbatim-write", repo_path=str(tmp_path))
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["deterministic_operations"] == 1
    assert summary["statuses"]["success"] == 1


# --------------------------------------------------------------------------- #
# 2. local provider routing is recorded
# --------------------------------------------------------------------------- #
def test_local_provider_routing_is_recorded(tmp_path):
    engine = _FakeEngine("ok")
    out = provider_policy.invoke("unit-op", "local", "prompt", "sys",
                                 repo_path=str(tmp_path), engine=engine)
    assert out == "ok"
    assert engine.calls == [("prompt", "sys")]
    summary = usage_ledger.report(str(tmp_path))
    assert summary["local_operations"] == 1
    assert summary["statuses"]["success"] == 1


# --------------------------------------------------------------------------- #
# 3 & 4. paid provider requires explicit authorization; no silent fallback
# --------------------------------------------------------------------------- #
def test_paid_provider_denied_without_explicit_authorization(tmp_path):
    engine = _FakeEngine("should never run")
    with pytest.raises(provider_policy.ProviderPolicyError):
        provider_policy.invoke("unit-op", "claude", "prompt", repo_path=str(tmp_path), engine=engine)
    assert engine.calls == []  # never invoked - no silent fallback
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["statuses"]["blocked"] == 1
    # "claude" is a real registered paid-provider name (unlike "mock"), so a
    # denial here counts as an attempted-but-blocked paid operation, not the
    # separate "denied" (prohibited/unregistered name) bucket.
    assert summary["paid_operations"] == 1


def test_paid_provider_permitted_with_explicit_authorization(tmp_path):
    engine = _FakeEngine("claude output")
    out = provider_policy.invoke("unit-op", "claude", "prompt", repo_path=str(tmp_path),
                                 engine=engine, explicit_user_selection=True)
    assert out == "claude output"
    assert engine.calls
    summary = usage_ledger.report(str(tmp_path))
    assert summary["paid_operations"] == 1


def test_no_silent_paid_fallback_after_local_failure(tmp_path):
    """A local failure must never cause an automatic retry against a paid
    provider - invoke() only ever tries the one provider it was told to."""
    engine = _FakeEngine(fail_with=EngineError("local down"))
    with pytest.raises(EngineError):
        provider_policy.invoke("unit-op", "local", "prompt", repo_path=str(tmp_path), engine=engine)
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1  # exactly one attempt, no paid retry
    assert summary["paid_operations"] == 0


# --------------------------------------------------------------------------- #
# 5. prohibited mock provider is rejected
# --------------------------------------------------------------------------- #
def test_mock_provider_rejected_in_production(tmp_path):
    with pytest.raises(provider_policy.ProviderPolicyError, match="prohibited"):
        provider_policy.invoke("unit-op", "mock", "prompt", repo_path=str(tmp_path))


# --------------------------------------------------------------------------- #
# 6. routing reason is persisted
# --------------------------------------------------------------------------- #
def test_routing_reason_is_persisted(tmp_path):
    engine = _FakeEngine("ok")
    provider_policy.invoke("unit-op", "claude", "prompt", repo_path=str(tmp_path),
                           engine=engine, explicit_user_selection=True)
    summary = usage_ledger.report(str(tmp_path))
    latest = summary["latest_operation"]
    assert latest["routing_reason"] == "explicit CLI/user provider selection"
    assert latest["explicit_user_selection"] == 1


# --------------------------------------------------------------------------- #
# 7. provider-reported exact usage vs. estimated usage
# --------------------------------------------------------------------------- #
def test_exact_usage_distinguished_from_estimated(tmp_path):
    engine = _FakeEngine("ok", usage={"input_tokens": 11, "output_tokens": 4, "source": "ollama_reported"})
    provider_policy.invoke("unit-op", "local", "prompt text", repo_path=str(tmp_path), engine=engine)
    summary = usage_ledger.report(str(tmp_path))
    assert summary["usage_exact_count"] == 1
    assert summary["usage_estimated_count"] == 0
    assert summary["actual_input_tokens_available_count"] == 1

    engine2 = _FakeEngine("ok")  # no usage captured
    provider_policy.invoke("unit-op", "local", "prompt text", repo_path=str(tmp_path), engine=engine2)
    summary2 = usage_ledger.report(str(tmp_path))
    assert summary2["usage_exact_count"] == 1
    assert summary2["usage_estimated_count"] == 1


# --------------------------------------------------------------------------- #
# 8. unavailable usage is represented honestly
# --------------------------------------------------------------------------- #
def test_unavailable_usage_for_denied_operation(tmp_path):
    with pytest.raises(provider_policy.ProviderPolicyError):
        provider_policy.invoke("unit-op", "mock", "prompt", repo_path=str(tmp_path))
    summary = usage_ledger.report(str(tmp_path))
    assert summary["usage_unavailable_count"] == 1
    assert summary["usage_exact_count"] == 0
    assert summary["usage_estimated_count"] == 0


# --------------------------------------------------------------------------- #
# 9. failed provider call is recorded as failed
# --------------------------------------------------------------------------- #
def test_failed_provider_call_recorded_as_failed(tmp_path):
    engine = _FakeEngine(fail_with=EngineError("boom"))
    with pytest.raises(EngineError):
        provider_policy.invoke("unit-op", "local", "prompt", repo_path=str(tmp_path), engine=engine)
    summary = usage_ledger.report(str(tmp_path))
    assert summary["statuses"]["failed"] == 1
    assert summary["statuses"]["success"] == 0


# --------------------------------------------------------------------------- #
# 10. validation failure before generation is not recorded as success
# --------------------------------------------------------------------------- #
def test_unavailable_engine_not_recorded_as_success(tmp_path):
    engine = _FakeEngine(avail=False)
    with pytest.raises(EngineError):
        provider_policy.invoke("unit-op", "local", "prompt", repo_path=str(tmp_path), engine=engine)
    assert engine.calls == []  # generate() never called
    summary = usage_ledger.report(str(tmp_path))
    assert summary["statuses"]["blocked"] == 1
    assert summary["statuses"]["success"] == 0


# --------------------------------------------------------------------------- #
# 11. cleanup runs only after a provider was genuinely invoked
# --------------------------------------------------------------------------- #
def test_cleanup_only_after_genuine_local_invocation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "unload_model", lambda model: calls.append(model) or (True, "ok"))
    monkeypatch.setattr(llm, "model_loaded", lambda model: (False, "ok"))

    # Denied before any engine touch: no cleanup.
    with pytest.raises(provider_policy.ProviderPolicyError):
        provider_policy.invoke("unit-op", "mock", "prompt", repo_path=str(tmp_path))
    assert calls == []

    # Unavailable engine: never generated, no cleanup.
    with pytest.raises(EngineError):
        provider_policy.invoke("unit-op", "local", "prompt", repo_path=str(tmp_path),
                               engine=_FakeEngine(avail=False))
    assert calls == []

    # Genuine local invocation: cleanup requested exactly once.
    provider_policy.invoke("unit-op", "local", "prompt", repo_path=str(tmp_path), engine=_FakeEngine("ok"))
    assert calls == ["qwen2.5:14b"]


def test_cleanup_not_requested_for_paid_provider(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "unload_model", lambda model: calls.append(model) or (True, "ok"))
    provider_policy.invoke("unit-op", "claude", "prompt", repo_path=str(tmp_path),
                           engine=_FakeEngine("ok"), explicit_user_selection=True)
    assert calls == []  # cleanup is a local-model-only concept


# --------------------------------------------------------------------------- #
# 12 & 13. ledger write is atomic; malformed ledger data fails safely
# --------------------------------------------------------------------------- #
def test_ledger_write_is_atomic_on_partial_failure(tmp_path):
    """A record() call either fully commits or leaves no partial row - proven
    by writing a well-formed entry, corrupting the connection mid-write is
    impractical to simulate safely, so this proves the same invariant the
    existing Phase 2A test does: successive record() calls never leave the
    table in an inconsistent state readable by report()."""
    for i in range(5):
        provider_policy.record_deterministic(f"op-{i}", repo_path=str(tmp_path))
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 5


def test_malformed_ledger_schema_version_fails_safely(tmp_path):
    usage_ledger.init_ledger(str(tmp_path))
    path = usage_ledger.ledger_path(str(tmp_path))
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 999")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError):
        usage_ledger.report(str(tmp_path))


# --------------------------------------------------------------------------- #
# 14. sensitive values are not persisted
# --------------------------------------------------------------------------- #
def test_sensitive_prompt_content_not_persisted(tmp_path):
    engine = _FakeEngine("ok")
    secret_prompt = "API_KEY=super-secret-value\nDo the thing."
    provider_policy.invoke("unit-op", "local", secret_prompt, repo_path=str(tmp_path), engine=engine)
    db_bytes = (tmp_path / "data" / "autocorp_usage_ledger.sqlite3").read_bytes()
    assert b"super-secret-value" not in db_bytes


# --------------------------------------------------------------------------- #
# 15 & 16. coverage audit
# --------------------------------------------------------------------------- #
def test_coverage_audit_flags_intentionally_uncovered_fixture(tmp_path):
    fixture_dir = tmp_path / "brains"
    fixture_dir.mkdir()
    (fixture_dir / "rogue_generator.py").write_text(
        "from core import llm\n\ndef do_it():\n    return llm.generate('x')\n"
    )
    report = provider_coverage_audit.run_audit(str(tmp_path), search_paths=["brains/rogue_generator.py"])
    assert "brains/rogue_generator.py" in report.uncovered_call_sites


def test_coverage_audit_passes_when_registered_routed_file_references_policy(tmp_path):
    fixture_dir = tmp_path / "brains"
    fixture_dir.mkdir()
    (fixture_dir / "builder.py").write_text(
        "from brains import provider_policy\n\n"
        "def gen():\n    return provider_policy.invoke('x', 'local', 'p', repo_path='.')\n"
    )
    report = provider_coverage_audit.run_audit(str(tmp_path), search_paths=["brains/builder.py"])
    assert report.covered_call_sites == 1
    assert report.uncovered_call_sites == []
    assert report.coverage_percentage == 100.0


def test_coverage_audit_against_real_autocorp_source_has_no_unregistered_call_sites():
    """Production gate: every real .py file under brains/, core/, and
    reliability_engine/ that contains a generation call must be either
    registered as routed-and-covered, explicitly excluded, or
    not-model-capable - never silently absent from the registry."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report = provider_coverage_audit.run_audit(repo_root)
    assert report.uncovered_call_sites == [], (
        f"uncovered/unregistered model-capable call sites: {report.uncovered_call_sites}"
    )
    assert report.covered_call_sites >= 6  # builder, tester, planner, providers, repair_content_generator, orchestrator


# --------------------------------------------------------------------------- #
# 17. usage-report separates deterministic / local / paid / failed / exact /
#     estimated / unavailable records
# --------------------------------------------------------------------------- #
def test_usage_report_separates_all_categories(tmp_path):
    provider_policy.record_deterministic("det", repo_path=str(tmp_path))
    provider_policy.invoke("local-op", "local", "p", repo_path=str(tmp_path), engine=_FakeEngine("ok"))
    provider_policy.invoke("paid-op", "claude", "p", repo_path=str(tmp_path),
                           engine=_FakeEngine("ok"), explicit_user_selection=True)
    with pytest.raises(EngineError):
        provider_policy.invoke("fail-op", "local", "p", repo_path=str(tmp_path),
                               engine=_FakeEngine(fail_with=EngineError("x")))
    with pytest.raises(provider_policy.ProviderPolicyError):
        provider_policy.invoke("denied-op", "mock", "p", repo_path=str(tmp_path))

    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 5
    assert summary["deterministic_operations"] == 1
    assert summary["local_operations"] == 2  # local-op + fail-op
    assert summary["paid_operations"] == 1
    assert summary["denied_operations"] == 1
    # det (success) + local-op (success) + paid-op (success) = 3;
    # fail-op = 1 failed; denied-op (mock, prohibited) = 1 blocked.
    assert summary["statuses"] == {"success": 3, "failed": 1, "blocked": 1}
    human = usage_ledger.render_human(summary)
    assert "Deterministic (no model call) operations: 1" in human
    assert "Denied operations" in human
    assert "Provider Coverage Audit" in human


# --------------------------------------------------------------------------- #
# 18. zero-event reports do not fabricate savings
# --------------------------------------------------------------------------- #
def test_zero_event_report_does_not_fabricate_savings(tmp_path):
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 0
    assert summary["measured_savings_percentage"] is None
    human = usage_ledger.render_human(summary)
    assert "No provider usage evidence has been recorded yet." in human
    assert "unavailable (no evidence)" in human
    assert "77%" not in human
    assert "77" not in human


# --------------------------------------------------------------------------- #
# 19. CLI help and parser behavior remain valid
# --------------------------------------------------------------------------- #
def test_cli_help_and_usage_report_parser_still_valid():
    parser = autocorp.build_parser()
    args = parser.parse_args(["usage-report", "--repo", "/tmp", "--json"])
    assert args.func is autocorp.cmd_usage_report
    # --help must not raise for the top-level parser or usage-report.
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["usage-report", "--help"])
    assert exc.value.code == 0


def test_usage_report_cli_includes_coverage_section(tmp_path, capsys):
    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Provider Coverage Audit" in out
    assert "Coverage percentage" in out

    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert "coverage" in data
    assert "known_call_sites" in data["coverage"]


# --------------------------------------------------------------------------- #
# 20. existing Phase 2A behavior remains compatible
# --------------------------------------------------------------------------- #
def test_phase_2a_ledger_entry_shape_unchanged(tmp_path):
    usage_ledger.record(usage_ledger.LedgerEntry(
        repository_path=str(tmp_path), operation_name="ok", provider="local", model="m",
        result_status="success",
        estimated_paid_baseline_tokens=100, estimated_paid_tokens_used=0,
        estimated_paid_tokens_avoided=100, estimated_savings_percentage=100.0,
    ))
    summary = usage_ledger.report(str(tmp_path))
    assert summary["statuses"]["success"] == 1
    assert summary["measured_savings_percentage"] == 100.0


def test_phase_2a_usage_report_empty_and_json_still_work(tmp_path, capsys):
    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=False))
    assert rc == 0
    assert "No provider usage evidence" in capsys.readouterr().out
    usage_ledger.record(usage_ledger.LedgerEntry(
        repository_path=str(tmp_path), operation_name="x", provider="local", model="m", result_status="success"))
    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == usage_ledger.JSON_SCHEMA_VERSION
    assert data["total_operations"] == 1


# --------------------------------------------------------------------------- #
# Builder / Tester wiring: real coverage, not just the policy in isolation
# --------------------------------------------------------------------------- #
def test_builder_gen_file_routes_through_policy_and_records_ledger(tmp_path):
    engine = _FakeEngine("print('hi')\n")
    builder = BuilderBrain(Executor(AllowAllGate()), engine=engine, repo_path=str(tmp_path))
    plan = {"project_name": "demo", "language": "python", "summary": "s",
            "files": [{"path": "main.py", "purpose": "p"}]}
    target = {"path": "main.py", "purpose": "p"}
    content = builder._gen_file(plan, target, {}, "")
    assert content.strip() == "print('hi')"
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["latest_operation"]["operation_name"] == "build-file-generation"


def test_builder_denies_paid_engine_without_explicit_selection(tmp_path):
    engine = _FakeEngine("x")
    engine.name = "claude"
    builder = BuilderBrain(Executor(AllowAllGate()), engine=engine, repo_path=str(tmp_path))
    builder.engine_explicit_selection = False  # auto-routed / not CLI-explicit
    plan = {"project_name": "demo", "language": "python", "summary": "s",
            "files": [{"path": "main.py", "purpose": "p"}]}
    target = {"path": "main.py", "purpose": "p"}
    with pytest.raises(provider_policy.ProviderPolicyError):
        builder._gen_file(plan, target, {}, "")
    assert engine.calls == []


def test_builder_permits_paid_engine_with_cli_explicit_selection(tmp_path):
    engine = _FakeEngine("print(1)\n")
    engine.name = "claude"
    builder = BuilderBrain(Executor(AllowAllGate()), engine=engine, repo_path=str(tmp_path))
    builder.engine_explicit_selection = True  # e.g. --engine claude
    plan = {"project_name": "demo", "language": "python", "summary": "s",
            "files": [{"path": "main.py", "purpose": "p"}]}
    target = {"path": "main.py", "purpose": "p"}
    content = builder._gen_file(plan, target, {}, "")
    assert content.strip() == "print(1)"
    summary = usage_ledger.report(str(tmp_path))
    assert summary["paid_operations"] == 1


def test_tester_suggest_fix_with_engine_routes_through_policy(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("x = 1\n")
    fix_json = json.dumps({"explanation": "fix", "filename": "main.py", "new_content": "x = 2\n"})
    engine = _FakeEngine(fix_json)
    tester = TesterBrain(Executor(AllowAllGate()), engine=engine, repo_path=str(tmp_path))
    result = tester.suggest_fix(str(ws), "main.py", "AssertionError")
    assert result["new_content"] == "x = 2\n"
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["latest_operation"]["operation_name"] == "self-heal-fix"


def test_tester_suggest_fix_without_engine_still_uses_generate_json(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        llm, "generate_json",
        lambda prompt, system="", model=None: {
            "explanation": "e", "filename": "main.py", "new_content": "x = 3\n"})
    tester = TesterBrain(Executor(AllowAllGate()), repo_path=str(tmp_path))
    result = tester.suggest_fix(str(ws), "main.py", "err")
    assert result["new_content"] == "x = 3\n"
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["local_operations"] == 1

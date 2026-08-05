#!/usr/bin/env python3
"""Tests for the Phase 2B VS Code Repair Handoff Generator
(brains/repair_handoff.py).

Every test here is fully offline: no Ollama, no Claude, no Codex, no
DeepSeek, no paid API, no network. The only subprocess calls are real,
local `git` inspection (deterministic, not a model call) and, where
explicitly tested, a fake `code` executable substituted via PATH - never
the real VS Code binary and never any submission to an agent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

import autocorp
from brains import provider_policy, repair_handoff, usage_ledger


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _git_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "app.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


VERIFIED_BROKEN_EVIDENCE = {
    "collected": 1, "passed": 0, "failed": 1, "exit_code": 1,
    "commands": [["pytest", "-q"]],
    "results": [{"test": "tests/test_app.py::test_add", "outcome": "failed",
                "message": "AssertionError: assert -0 == 4"}],
}

PASSED_EVIDENCE = {
    "collected": 3, "passed": 3, "failed": 0, "exit_code": 0,
    "commands": [["pytest", "-q"]], "results": [],
}

INCONCLUSIVE_ZERO_COLLECTED = {"collected": 0, "passed": 0, "failed": 0, "exit_code": 5, "commands": [], "results": []}

INCONCLUSIVE_FAILED_NO_DETAIL = {
    "collected": 2, "passed": 1, "failed": 1, "exit_code": 1,
    "commands": [["pytest", "-q"]], "results": [],
}


@pytest.fixture
def repo(tmp_path):
    return _git_repo(tmp_path)


# --------------------------------------------------------------------------- #
# 1 & 2. verified failure generates Codex / Claude prompts
# --------------------------------------------------------------------------- #
def test_verified_failure_generates_codex_prompt(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    assert os.path.isfile(record.prompt_path)
    text = _read(record.prompt_path)
    assert "Codex" in text
    assert "Codex Workflow" in text


def test_verified_failure_generates_claude_prompt(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="claude")
    assert os.path.isfile(record.prompt_path)
    text = _read(record.prompt_path)
    assert "Claude" in text
    assert "Claude Workflow" in text


# --------------------------------------------------------------------------- #
# 3. both mode creates two distinct files
# --------------------------------------------------------------------------- #
def test_both_mode_creates_two_distinct_files(repo):
    records = repair_handoff.generate_handoffs(str(repo), VERIFIED_BROKEN_EVIDENCE, agents=["codex", "claude"])
    assert len(records) == 2
    assert records[0].prompt_path != records[1].prompt_path
    codex_text = _read(records[0].prompt_path)
    claude_text = _read(records[1].prompt_path)
    assert codex_text != claude_text
    assert "Codex Workflow" in codex_text and "Codex Workflow" not in claude_text
    assert "Claude Workflow" in claude_text and "Claude Workflow" not in codex_text


# --------------------------------------------------------------------------- #
# 4, 5, 6. passing/inconclusive/warnings-only never produce a repair task
# --------------------------------------------------------------------------- #
def test_passing_result_generates_no_repair_task(repo):
    with pytest.raises(repair_handoff.RepairHandoffNotVerified):
        repair_handoff.generate_handoff(str(repo), PASSED_EVIDENCE, agent="codex")
    assert not os.path.isdir(repo / "AI_ENGINEERING" / "REPAIR_HANDOFFS")


def test_inconclusive_zero_collected_is_not_verified_broken(repo):
    verdict = repair_handoff.classify_evidence(INCONCLUSIVE_ZERO_COLLECTED)
    assert verdict.status == repair_handoff.INCONCLUSIVE
    with pytest.raises(repair_handoff.RepairHandoffNotVerified):
        repair_handoff.generate_handoff(str(repo), INCONCLUSIVE_ZERO_COLLECTED, agent="codex")


def test_warnings_alone_do_not_become_confirmed_defects(repo):
    evidence = dict(PASSED_EVIDENCE, uncertainty_warnings=["something looked odd"],
                    performance_warnings=["slow test"])
    verdict = repair_handoff.classify_evidence(evidence)
    assert verdict.status == repair_handoff.PASSED
    with pytest.raises(repair_handoff.RepairHandoffNotVerified):
        repair_handoff.generate_handoff(str(repo), evidence, agent="codex")


def test_failed_count_without_detail_is_inconclusive_not_fabricated():
    verdict = repair_handoff.classify_evidence(INCONCLUSIVE_FAILED_NO_DETAIL)
    assert verdict.status == repair_handoff.INCONCLUSIVE
    assert verdict.failing_tests == []


# --------------------------------------------------------------------------- #
# 7 & 8. exact failed command / exit code / traceback evidence preserved
# --------------------------------------------------------------------------- #
def test_failed_command_and_exit_code_preserved(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    text = _read(record.prompt_path)
    assert "pytest -q" in text
    assert "Exit code: 1" in text


def test_traceback_evidence_preserved(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    text = _read(record.prompt_path)
    assert "AssertionError: assert -0 == 4" in text
    assert "tests/test_app.py::test_add" in text


# --------------------------------------------------------------------------- #
# 9. suspected root causes are labeled as hypotheses
# --------------------------------------------------------------------------- #
def test_hypotheses_are_labeled_not_asserted_as_fact(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    text = _read(record.prompt_path)
    assert "Hypotheses (NOT verified facts" in text
    assert "AutoCorp does not assert a root cause" in text


# --------------------------------------------------------------------------- #
# 10. required project rules appear in every prompt
# --------------------------------------------------------------------------- #
def test_project_rules_appear_in_every_prompt(repo):
    for agent in ("codex", "claude"):
        record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent=agent)
        text = _read(record.prompt_path)
        for rule in repair_handoff.PROJECT_RULES:
            assert rule in text, f"missing rule '{rule}' in {agent} prompt"


# --------------------------------------------------------------------------- #
# 11. unrelated files are excluded from the authorized repair scope
# --------------------------------------------------------------------------- #
def test_unrelated_files_excluded_from_scope(repo):
    (repo / "unrelated_module.py").write_text("x = 1\n")
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    text = _read(record.prompt_path)
    scope_section = text.split("## Repair Scope")[1].split("## Required Project Rules")[0]
    assert "tests/test_app.py" in scope_section
    assert "unrelated_module.py" not in scope_section


def test_unknown_permissions_default_to_prohibited(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    text = _read(record.prompt_path)
    assert "Database migrations: PROHIBITED" in text
    assert "Dependency changes: PROHIBITED" in text
    assert "Network or model/API calls: PROHIBITED" in text
    assert "Production data access: PROHIBITED" in text


# --------------------------------------------------------------------------- #
# 12, 13, 14, 15. deterministic, no model, no network, no Ollama/paid call
# --------------------------------------------------------------------------- #
def test_generation_makes_no_model_or_network_call(repo, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network/model call attempted during deterministic handoff generation")
    monkeypatch.setattr("core.llm.generate", _boom)
    monkeypatch.setattr("core.llm.generate_json", _boom)
    monkeypatch.setattr("core.llm.generate_with_usage", _boom)
    monkeypatch.setattr("requests.post", _boom)
    monkeypatch.setattr("requests.get", _boom)
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    assert os.path.isfile(record.prompt_path)


def test_generation_recorded_as_deterministic_no_paid_call(repo):
    repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    summary = usage_ledger.report(str(repo))
    assert summary["total_operations"] == 1
    assert summary["deterministic_operations"] == 1
    assert summary["paid_operations"] == 0
    assert summary["local_operations"] == 0
    assert summary["measured_savings_percentage"] is None  # no fabricated savings


# --------------------------------------------------------------------------- #
# 16 & 17. atomic writes; existing handoffs not silently overwritten
# --------------------------------------------------------------------------- #
def test_write_is_atomic_no_tmp_file_left_behind(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    out_dir = os.path.dirname(record.prompt_path)
    leftovers = [f for f in os.listdir(out_dir) if ".tmp-" in f]
    assert leftovers == []


def test_existing_handoff_not_silently_overwritten(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex",
                                             now=1000000000.0)
    original = _read(record.prompt_path)
    with pytest.raises(FileExistsError):
        repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex", now=1000000000.0)
    assert _read(record.prompt_path) == original  # untouched


# --------------------------------------------------------------------------- #
# 18. filenames are filesystem-safe
# --------------------------------------------------------------------------- #
def test_filenames_are_filesystem_safe(repo):
    weird_evidence = dict(VERIFIED_BROKEN_EVIDENCE)
    weird_evidence["results"] = [{
        "test": "tests/weird test!!.py::test with spaces & stuff",
        "outcome": "failed", "message": "boom",
    }]
    record = repair_handoff.generate_handoff(str(repo), weird_evidence, agent="codex")
    filename = os.path.basename(record.prompt_path)
    assert re_allowed(filename)


def re_allowed(name: str) -> bool:
    import re
    return re.fullmatch(r"[A-Za-z0-9._-]+", name) is not None


# --------------------------------------------------------------------------- #
# 19. prompt hash matches written content
# --------------------------------------------------------------------------- #
def test_prompt_hash_matches_written_content(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    actual = hashlib.sha256(_read_bytes(record.prompt_path)).hexdigest()
    assert actual == record.prompt_sha256


# --------------------------------------------------------------------------- #
# 20. provenance is persisted
# --------------------------------------------------------------------------- #
def test_provenance_is_persisted(repo):
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex",
                                             run_id="fixed-run-id")
    prov_path = record.prompt_path + ".provenance.json"
    assert os.path.isfile(prov_path)
    data = json.loads(_read(prov_path))
    assert data["source_run_identifier"] == "fixed-run-id"
    assert data["generated_prompt_sha256"] == record.prompt_sha256
    assert data["target_agent"] == "codex"
    assert data["generation_method"] == "deterministic-template"


# --------------------------------------------------------------------------- #
# 21 & 22. secrets / API keys are redacted
# --------------------------------------------------------------------------- #
def test_secrets_redacted_from_prompt(repo):
    evidence = dict(VERIFIED_BROKEN_EVIDENCE)
    evidence["results"] = [{
        "test": "tests/test_app.py::test_add", "outcome": "failed",
        "message": 'boom: API_KEY=sk-live-abcdef123456 and {"Authorization": "Bearer supersecrettoken123"}',
    }]
    record = repair_handoff.generate_handoff(str(repo), evidence, agent="codex")
    text = _read(record.prompt_path)
    assert "sk-live-abcdef123456" not in text
    assert "supersecrettoken123" not in text
    assert "REDACTED" in text


def test_no_api_keys_appear_in_generated_files(repo):
    os.environ.setdefault("_UNUSED_TEST_MARKER", "1")
    evidence = dict(VERIFIED_BROKEN_EVIDENCE)
    evidence["results"] = [{
        "test": "tests/test_app.py::test_add", "outcome": "failed",
        "message": "DB_PASSWORD=hunter2hunter2 leaked",
    }]
    record = repair_handoff.generate_handoff(str(repo), evidence, agent="claude")
    text = _read(record.prompt_path)
    assert "hunter2hunter2" not in text


# --------------------------------------------------------------------------- #
# 23, 24, 25, 26. VS Code integration
# --------------------------------------------------------------------------- #
def _fake_code_binary(tmp_path, *, succeed: bool):
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "code"
    body = "#!/bin/sh\necho \"$@\" > \"$(dirname \"$0\")/last_call.txt\"\n"
    body += "exit 0\n" if succeed else "exit 1\n"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(tmp_path)


def test_vscode_receives_real_absolute_prompt_path(repo, tmp_path, monkeypatch):
    bin_dir = _fake_code_binary(tmp_path / "bin", succeed=True)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    result = repair_handoff.open_in_vscode(record.prompt_path)
    assert result["success"] is True
    called_with = (tmp_path / "bin" / "last_call.txt").read_text().strip()
    assert "--reuse-window" in called_with
    assert record.prompt_path in called_with


def test_successful_vscode_execution_reported_truthfully(repo, tmp_path, monkeypatch):
    bin_dir = _fake_code_binary(tmp_path / "bin2", succeed=True)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    result = repair_handoff.open_in_vscode(record.prompt_path)
    assert result["attempted"] is True
    assert result["success"] is True


def test_failed_vscode_execution_reported_truthfully(repo, tmp_path, monkeypatch):
    bin_dir = _fake_code_binary(tmp_path / "bin3", succeed=False)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    result = repair_handoff.open_in_vscode(record.prompt_path)
    assert result["attempted"] is True
    assert result["success"] is False


def test_missing_code_command_still_leaves_usable_file(repo, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-bin-dir-for-test")
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    result = repair_handoff.open_in_vscode(record.prompt_path)
    assert result["attempted"] is False
    assert result["success"] is False
    assert os.path.isfile(record.prompt_path)  # the handoff itself is still usable


def test_vscode_integration_never_submits_prompt_to_agent(repo, tmp_path, monkeypatch):
    """The fake `code` binary only ever receives --reuse-window and the file
    path - nothing resembling agent submission (no stdin piping of prompt
    content, no extension-specific flags)."""
    bin_dir = _fake_code_binary(tmp_path / "bin4", succeed=True)
    monkeypatch.setenv("PATH", bin_dir + os.pathsep + os.environ["PATH"])
    record = repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    repair_handoff.open_in_vscode(record.prompt_path)
    called_with = (tmp_path / "bin4" / "last_call.txt").read_text().strip()
    assert called_with == f"--reuse-window {record.prompt_path}"


# --------------------------------------------------------------------------- #
# 27. deterministic generation requires no model/network - reproducibility
# --------------------------------------------------------------------------- #
def test_deterministic_output_for_fixed_evidence_and_identifiers(repo):
    r1 = repair_handoff.generate_handoff(
        str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex",
        run_id="fixed-id", now=1000000000.0,
    )
    content1 = _read_bytes(r1.prompt_path)
    os.remove(r1.prompt_path)
    os.remove(r1.prompt_path + ".provenance.json")
    r2 = repair_handoff.generate_handoff(
        str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex",
        run_id="fixed-id", now=1000000000.0,
    )
    content2 = _read_bytes(r2.prompt_path)
    assert content1 == content2
    assert r1.prompt_sha256 == r2.prompt_sha256


# --------------------------------------------------------------------------- #
# 28. CLI help / parser behavior
# --------------------------------------------------------------------------- #
def test_cli_parser_accepts_repair_handoff():
    parser = autocorp.build_parser()
    args = parser.parse_args([
        "repair-handoff", "--repo", "/tmp/x", "--evidence", "/tmp/e.json",
        "--agent", "both", "--open-vscode",
    ])
    assert args.func is autocorp.cmd_repair_handoff
    assert args.agent == "both"
    assert args.open_vscode is True


def test_cli_help_valid_for_repair_handoff():
    parser = autocorp.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["repair-handoff", "--help"])
    assert exc.value.code == 0


def test_cli_end_to_end_generates_handoff(repo, tmp_path, capsys):
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(VERIFIED_BROKEN_EVIDENCE))
    rc = autocorp.cmd_repair_handoff(argparse.Namespace(
        repo=str(repo), evidence=str(evidence_path), agent="codex", open_vscode=False,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "SHA-256" in out
    assert "AI_ENGINEERING/REPAIR_HANDOFFS" in out


def test_cli_rejects_missing_evidence_file(repo, capsys):
    rc = autocorp.cmd_repair_handoff(argparse.Namespace(
        repo=str(repo), evidence="/nonexistent/evidence.json", agent="codex", open_vscode=False,
    ))
    assert rc == 2


def test_cli_passed_evidence_returns_nonzero_without_creating_files(repo, tmp_path):
    evidence_path = tmp_path / "passed.json"
    evidence_path.write_text(json.dumps(PASSED_EVIDENCE))
    rc = autocorp.cmd_repair_handoff(argparse.Namespace(
        repo=str(repo), evidence=str(evidence_path), agent="codex", open_vscode=False,
    ))
    assert rc == 1
    assert not os.path.isdir(repo / "AI_ENGINEERING" / "REPAIR_HANDOFFS")


# --------------------------------------------------------------------------- #
# 29. Phase 2A and existing Phase 2B routing behavior remains compatible
# --------------------------------------------------------------------------- #
def test_phase_2a_and_2b_ledger_still_work_alongside_handoff(repo):
    repair_handoff.generate_handoff(str(repo), VERIFIED_BROKEN_EVIDENCE, agent="codex")
    provider_policy.record_deterministic("unrelated-op", repo_path=str(repo))
    summary = usage_ledger.report(str(repo))
    assert summary["total_operations"] == 2
    assert summary["deterministic_operations"] == 2
    human = usage_ledger.render_human(summary)
    assert "Provider Coverage Audit" in human  # Phase 2B section still renders

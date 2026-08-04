import argparse
import json
import os
import subprocess

import pytest

import autocorp
from brains import context_budget, repo_policy, usage_ledger
from brains.repair_content_generator import LocalRepairContentProvider, RepairContentProviderFactory
from brains.live_readiness import run_live_readiness


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_shared_generated_path_exclusion_and_tracked_docs(tmp_path):
    _write(tmp_path / "README.md", "# docs\n")
    _write(tmp_path / "app.py", "print('ok')\n")
    _git(tmp_path)
    assert repo_policy.classify_path(str(tmp_path), "data/session.json").excluded
    assert repo_policy.classify_path(str(tmp_path), "workspace/generated/app.py").excluded
    assert repo_policy.classify_path(str(tmp_path), ".reliability_worktrees/x/app.py").excluded
    assert not repo_policy.classify_path(str(tmp_path), "README.md").excluded
    assert not repo_policy.classify_path(str(tmp_path), "app.py").excluded


def test_runtime_data_does_not_dirty_source_scan(tmp_path):
    _write(tmp_path / "app.py", "print('ok')\n")
    _write(tmp_path / "data" / "bad.py", "raise NotImplementedError\n")
    _write(tmp_path / "workspace" / "bad.py", "raise NotImplementedError\n")
    files = [rel for _full, rel, _cls in repo_policy.walk_source_files(str(tmp_path), suffixes=(".py",))]
    assert files == ["app.py"]


def test_abstract_methods_do_not_fail_readiness_but_concrete_does(tmp_path):
    _write(
        tmp_path / "abstracts.py",
        "from abc import ABC, abstractmethod\n"
        "class Base(ABC):\n"
        "    @abstractmethod\n"
        "    def generate(self):\n"
        "        raise NotImplementedError\n",
    )
    _git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    assert not [c for c in report.checks if "NotImplemented" in c.title]
    _write(tmp_path / "concrete.py", "def f():\n    raise NotImplementedError\n")
    report = run_live_readiness(str(tmp_path))
    assert [c for c in report.checks if "NotImplemented" in c.title]


def test_comments_and_tests_notimplemented_are_not_production_blockers(tmp_path):
    _write(tmp_path / "app.py", "text = 'NotImplementedError'\n# NotImplementedError\n")
    _write(tmp_path / "tests" / "test_app.py", "def test_x():\n    raise NotImplementedError\n")
    _git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    assert not [c for c in report.checks if "NotImplemented" in c.title]


def test_context_selection_budget_fingerprint_and_secret_exclusion(tmp_path):
    _write(tmp_path / "app.py", "import helper\nVALUE = 1\n")
    _write(tmp_path / "helper.py", "HELPER = 1\n")
    _write(tmp_path / "tests" / "test_app.py", "import app\ndef test_value(): assert app.VALUE == 1\n")
    _write(tmp_path / ".env", "API_KEY=secret\n")
    _git(tmp_path)
    prompt, manifest = context_budget.build_repair_context(
        str(tmp_path), "app.py", "failure", provider="local", model="m", max_prompt_bytes=2000
    )
    paths = [i["path"] for i in manifest.items]
    assert paths[0] == "app.py"
    assert "tests/test_app.py" in paths
    assert ".env" not in paths
    assert manifest.total_prompt_bytes == len(prompt.encode("utf-8"))
    before = manifest.context_fingerprint
    _write(tmp_path / "app.py", "import helper\nVALUE = 2\n")
    _prompt2, manifest2 = context_budget.build_repair_context(str(tmp_path), "app.py", "failure", provider="local", model="m")
    assert manifest2.context_fingerprint != before


def test_context_budget_enforced(tmp_path):
    _write(tmp_path / "app.py", "x = '" + ("a" * 5000) + "'\n")
    _write(tmp_path / "tests" / "test_app.py", "def test_x(): pass\n")
    _git(tmp_path)
    _prompt, manifest = context_budget.build_repair_context(
        str(tmp_path), "app.py", "failure", provider="local", model="m", max_prompt_bytes=900, max_file_bytes=200
    )
    assert manifest.omitted_candidate_count >= 0
    assert manifest.truncation_warnings


def test_cache_key_rejects_relevant_changes(tmp_path):
    _write(tmp_path / "app.py", "x = 1\n")
    _git(tmp_path)
    _p1, m1 = context_budget.build_repair_context(str(tmp_path), "app.py", "failure", provider="local", model="m")
    _p2, m2 = context_budget.build_repair_context(str(tmp_path), "app.py", "different", provider="local", model="m")
    assert m1.context_fingerprint != m2.context_fingerprint


class _Engine:
    name = "local"
    calls = []

    def available(self):
        return True

    def generate(self, prompt, system=""):
        self.calls.append((prompt, system))
        return "x = 2\n"


def test_local_repair_provider_real_local_engine_call_and_ledger(monkeypatch, tmp_path):
    _write(tmp_path / "app.py", "x = 1\n")
    _git(tmp_path)
    monkeypatch.setattr("core.llm.unload_model", lambda model: (True, "ok"))
    monkeypatch.setattr("core.llm.model_loaded", lambda model: (False, "ok"))
    engine = _Engine()
    provider = LocalRepairContentProvider(str(tmp_path), model="m", engine=engine)
    assert provider.generate("app.py", "fix x") == "x = 2\n"
    assert engine.calls
    summary = usage_ledger.report(str(tmp_path))
    assert summary["total_operations"] == 1
    assert summary["local_operations"] == 1
    assert summary["local_model_cleanup_failures"] == 0


def test_local_provider_rejects_unsafe_paths_and_mock_provider(monkeypatch, tmp_path):
    _write(tmp_path / "app.py", "x = 1\n")
    _git(tmp_path)
    unload_calls = []

    def _unload(model):
        unload_calls.append(model)
        return True, "ok"

    monkeypatch.setattr("core.llm.unload_model", _unload)
    monkeypatch.setattr("core.llm.model_loaded", lambda model: (False, "ok"))
    provider = LocalRepairContentProvider(str(tmp_path), model="m", engine=_Engine())
    with pytest.raises(ValueError):
        provider.generate("../outside.py", "no")
    assert unload_calls == []
    with pytest.raises(ValueError):
        RepairContentProviderFactory.create("mock")


def test_ledger_success_failed_blocked_and_no_raw_secret(tmp_path):
    secret_manifest = {"items": [{"path": "app.py", "content_sha256": "abc"}], "prompt": "API_KEY=secret"}
    usage_ledger.record(usage_ledger.LedgerEntry(
        repository_path=str(tmp_path), operation_name="ok", provider="local", model="m",
        result_status="success", selected_context_manifest=secret_manifest,
        estimated_paid_baseline_tokens=100, estimated_paid_tokens_used=0,
        estimated_paid_tokens_avoided=100, estimated_savings_percentage=100.0,
    ))
    usage_ledger.record(usage_ledger.LedgerEntry(repository_path=str(tmp_path), operation_name="bad", provider="local", model="m", result_status="failed"))
    usage_ledger.record(usage_ledger.LedgerEntry(repository_path=str(tmp_path), operation_name="blocked", provider="deepseek", model="m", result_status="blocked"))
    summary = usage_ledger.report(str(tmp_path))
    assert summary["statuses"] == {"success": 1, "failed": 1, "blocked": 1}
    db_text = (tmp_path / "data" / "autocorp_usage_ledger.sqlite3").read_bytes()
    assert b"API_KEY=secret" not in db_text


def test_usage_report_empty_human_and_json(capsys, tmp_path):
    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=False))
    assert rc == 0
    assert "No provider usage evidence" in capsys.readouterr().out
    usage_ledger.record(usage_ledger.LedgerEntry(repository_path=str(tmp_path), operation_name="x", provider="local", model="m", result_status="success"))
    rc = autocorp.cmd_usage_report(argparse.Namespace(repo=str(tmp_path), json=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["schema_version"] == usage_ledger.JSON_SCHEMA_VERSION
    assert data["total_operations"] == 1

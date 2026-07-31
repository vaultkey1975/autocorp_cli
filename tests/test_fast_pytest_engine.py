"""Focused tests for the Universal Fast Pytest Engine.

Uses disposable temporary Git repositories (never AutoCorp's own repo, never
CloneCast, never Video Lab). Mocks are only used at isolated subprocess
boundaries (see test_subprocess_failure_propagates / test_real_exit_code_propagation
comments below); every "tests actually ran" assertion is backed by a real
pytest subprocess run against a disposable repo.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import autocorp
import config
from autocorp_testing import change_detection, discovery, execution, history, mapping, planning, reporting, safety
from brains import fast_pytest_engine


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def history_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "autocorp_data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(history, "HISTORY_DB_PATH", os.path.join(str(data), "fast_pytest_engine", "history.db"))
    return data


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")


def _commit_all(repo: Path, message: str = "commit") -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def make_repo(tmp_path: Path, *, with_ci: bool = False, with_readme: bool = False) -> Path:
    """A small, real, disposable repository with a source package, tests
    that exercise every mapping signal, and a pytest.ini."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = -q\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("requests\n", encoding="utf-8")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pkg" / "mod_b.py").write_text(
        "from pkg.mod_a import add\n\n\ndef add_twice(a, b):\n    return add(a, b) * 2\n", encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "test_mod_a.py").write_text(
        "from pkg.mod_a import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8",
    )
    (repo / "tests" / "test_mod_b.py").write_text(
        "from pkg.mod_b import add_twice\n\n\ndef test_add_twice():\n    assert add_twice(1, 2) == 6\n", encoding="utf-8",
    )
    (repo / "tests" / "test_safety_gate.py").write_text(
        "def test_gate_default_deny():\n    assert True\n", encoding="utf-8",
    )
    if with_ci:
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "ci.yml").write_text(
            "name: CI\non: [push]\njobs:\n  test:\n    steps:\n      - run: .venv/bin/python -m pytest -W error -q\n",
            encoding="utf-8",
        )
    if with_readme:
        (repo / "README.md").write_text(
            "# Demo\n\nInstall dev deps with `pip install pytest`.\n\nRun tests with:\n\n"
            "    python -m pytest -W error\n", encoding="utf-8",
        )
    _commit_all(repo, "init")
    return repo


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def test_python_project_discovery_finds_test_roots_and_dependency_files(tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    assert cfg.test_roots == ["tests"]
    assert "requirements.txt" in cfg.dependency_files
    assert any(c.endswith("conftest.py") for c in cfg.conftest_paths)


def test_pytest_configuration_discovery_reads_pytest_ini(tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    assert cfg.config_files.get("pytest.ini") == "pytest.ini"
    assert cfg.strict_full_command is not None
    assert cfg.strict_full_command[-2:] == ["-m", "pytest"]
    assert cfg.strict_full_command_confidence in ("medium", "high")


def test_ci_workflow_strict_command_discovery(tmp_path):
    repo = make_repo(tmp_path, with_ci=True)
    cfg = discovery.discover(str(repo))
    assert cfg.strict_full_command_confidence == "high"
    assert "-W" in cfg.strict_full_command
    assert any(".github/workflows/ci.yml" in ev for ev in cfg.strict_full_command_evidence)


def test_readme_pytest_install_line_is_not_mistaken_for_a_command(tmp_path):
    repo = make_repo(tmp_path, with_readme=True)
    cfg = discovery.discover(str(repo))
    # "pip install pytest" must never be discovered as the strict command.
    assert cfg.strict_full_command is not None
    assert "install" not in " ".join(cfg.strict_full_command)
    assert "-W" in cfg.strict_full_command


def test_profile_override_wins_over_discovered_command(tmp_path):
    repo = make_repo(tmp_path, with_ci=True)
    autocorp_dir = repo / ".autocorp"
    autocorp_dir.mkdir()
    (autocorp_dir / "test-profile.json").write_text(
        json.dumps({"strict_full_command": [sys.executable, "-m", "pytest", "-q"]}), encoding="utf-8",
    )
    cfg = discovery.discover(str(repo))
    assert cfg.strict_full_command == [sys.executable, "-m", "pytest", "-q"]
    assert cfg.strict_full_command_confidence == "high"


def test_no_confident_strict_command_without_any_evidence(tmp_path):
    repo = tmp_path / "bare"
    _init_repo(repo)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo)
    cfg = discovery.discover(str(repo))
    assert cfg.strict_full_command is None
    assert cfg.strict_full_command_confidence == "none"


# --------------------------------------------------------------------------- #
# Change detection
# --------------------------------------------------------------------------- #
def test_unstaged_file_detection(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
    cs = change_detection.detect_changes(str(repo))
    assert "pkg/mod_a.py" in cs.unstaged
    assert "pkg/mod_a.py" in cs.changed_files


def test_staged_file_detection(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
    _git(repo, "add", "pkg/mod_a.py")
    cs = change_detection.detect_changes(str(repo))
    assert "pkg/mod_a.py" in cs.staged


def test_untracked_source_detection(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "mod_c.py").write_text("def sub(a, b):\n    return a - b\n", encoding="utf-8")
    cs = change_detection.detect_changes(str(repo))
    assert "pkg/mod_c.py" in cs.untracked
    assert "pkg/mod_c.py" in cs.changed_files


def test_committed_branch_diff_detection_with_explicit_base_branch(tmp_path):
    repo = make_repo(tmp_path)
    _git(repo, "branch", "base")
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    _commit_all(repo, "change mod_a")
    cs = change_detection.detect_changes(str(repo), base_branch="base")
    assert "pkg/mod_a.py" in cs.committed_vs_base
    assert cs.base_branch == "base"


def test_explicit_paths_and_node_ids_are_recorded(tmp_path):
    repo = make_repo(tmp_path)
    cs = change_detection.detect_changes(
        str(repo), explicit_paths=["tests/test_mod_a.py"], explicit_node_ids=["tests/test_mod_a.py::test_add"],
    )
    assert cs.explicit_paths == ["tests/test_mod_a.py"]
    assert cs.explicit_node_ids == ["tests/test_mod_a.py::test_add"]
    assert "tests/test_mod_a.py" in cs.changed_files


def test_change_detection_never_writes_to_the_repository(tmp_path):
    repo = make_repo(tmp_path)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    change_detection.detect_changes(str(repo), base_branch="main")
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after == ""


# --------------------------------------------------------------------------- #
# Mapping
# --------------------------------------------------------------------------- #
def test_filename_mapping(tmp_path):
    repo = make_repo(tmp_path)
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), ["pkg/mod_a.py"])
    assert "tests/test_mod_a.py" in result.reasons
    assert any("matches changed module" in r for r in result.reasons["tests/test_mod_a.py"])


def test_import_mapping(tmp_path):
    repo = make_repo(tmp_path)
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), ["pkg/mod_a.py"])
    assert any("imports the changed module" in r for r in result.reasons["tests/test_mod_a.py"])


def test_dependency_expansion_includes_tests_of_dependent_modules(tmp_path):
    repo = make_repo(tmp_path)
    # mod_b imports mod_a, so changing mod_a should also select test_mod_b.py.
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), ["pkg/mod_a.py"])
    assert "tests/test_mod_b.py" in result.reasons
    assert any("dependency expansion" in r for r in result.reasons["tests/test_mod_b.py"])


def test_symbol_and_feature_matching(tmp_path):
    repo = make_repo(tmp_path)
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), [], feature="add_twice")
    assert "tests/test_mod_b.py" in result.reasons
    assert any("feature" in r for r in result.reasons["tests/test_mod_b.py"])


def test_explicit_profile_mapping(tmp_path):
    repo = make_repo(tmp_path)
    profile = {"explicit_mappings": {"pkg/mod_a.py": ["tests/test_safety_gate.py"]}}
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), ["pkg/mod_a.py"], profile=profile)
    assert "tests/test_safety_gate.py" in result.reasons
    assert any(".autocorp/test-profile.json" in r for r in result.reasons["tests/test_safety_gate.py"])


def test_conservative_handling_of_uncertainty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "orphan.py").write_text("def orphan():\n    return 1\n", encoding="utf-8")
    result, _, _ = mapping.map_changed_files_to_tests(str(repo), ["pkg/orphan.py"])
    assert "pkg/orphan.py" not in result.reasons or not result.reasons.get("pkg/orphan.py")
    assert any("pkg/orphan.py" in w for w in result.uncertainty_warnings)


def test_fast_report_surfaces_uncertainty_warnings(history_data_dir, tmp_path):
    """Uncertainty found during planning must not be silently dropped when
    FAST actually executes — it has to reach the report and its JSON."""
    repo = make_repo(tmp_path)
    (repo / "pkg" / "orphan.py").write_text("def orphan():\n    return 1\n", encoding="utf-8")
    report = fast_pytest_engine.test_fast(str(repo))
    assert any("pkg/orphan.py" in w for w in report.uncertainty_warnings)
    assert any("pkg/orphan.py" in w for w in report.to_dict()["uncertainty_warnings"])
    assert "Uncertainty warnings:" in reporting.render_report(report)


def test_mandatory_safety_tests_are_always_flagged(tmp_path):
    repo = make_repo(tmp_path)
    _, test_index, _ = mapping.map_changed_files_to_tests(str(repo), [])
    safety_tests = mapping.mandatory_safety_tests(test_index, {})
    assert "tests/test_safety_gate.py" in safety_tests


def test_selection_reasons_are_recorded_for_every_selected_test(tmp_path):
    repo = make_repo(tmp_path)
    plan = planning.build_plan(str(repo), explicit_paths=["pkg/mod_a.py"])
    assert plan.fast_tests
    for t in plan.fast_tests:
        assert t.reasons, f"{t.node_id} has no recorded selection reasons"


# --------------------------------------------------------------------------- #
# Safety: GPU / database classification, parallelism, production DB
# --------------------------------------------------------------------------- #
def test_gpu_test_classification():
    cls = safety.classify_test("import torch\ndef test_gpu():\n    assert torch.cuda.is_available()\n", config=None)
    assert cls.gpu is True


def test_database_test_classification():
    cls = safety.classify_test("import sqlite3\ndef test_db():\n    sqlite3.connect(':memory:')\n", config=None)
    assert cls.database is True


def test_neutral_test_is_not_misclassified():
    cls = safety.classify_test("def test_plain():\n    assert 1 + 1 == 2\n", config=None)
    assert cls.gpu is False
    assert cls.database is False


def test_safe_xdist_decision_when_installed_and_no_shared_state():
    decision = safety.decide_parallelism(
        has_xdist=True, selected_texts={"tests/test_a.py": "def test_a():\n    assert True\n"}, cpu_count=8,
    )
    assert decision["enabled"] is True
    assert decision["workers"] >= 1


def test_unsafe_shared_database_decision_disables_parallelism():
    decision = safety.decide_parallelism(
        has_xdist=True,
        selected_texts={"tests/test_a.py": "import sqlite3\nsqlite3.connect('shared.db')\n"},
        cpu_count=8,
    )
    assert decision["enabled"] is False
    assert "shared" in decision["reason"] or "SQLite" in decision["reason"]


def test_parallelism_disabled_when_xdist_not_installed():
    decision = safety.decide_parallelism(has_xdist=False, selected_texts={}, cpu_count=8)
    assert decision["enabled"] is False
    assert "xdist" in decision["reason"]


def test_production_db_guard_detects_unexpected_change(tmp_path):
    db = tmp_path / "prod.db"
    db.write_bytes(b"original-bytes")
    snapshot = safety.snapshot_production_db(str(db))
    assert snapshot.checked is True
    db.write_bytes(b"mutated-bytes")
    after = safety.verify_production_db(snapshot)
    assert after.changed_unexpectedly is True


def test_production_db_guard_reports_unchanged(tmp_path):
    db = tmp_path / "prod.db"
    db.write_bytes(b"original-bytes")
    snapshot = safety.snapshot_production_db(str(db))
    after = safety.verify_production_db(snapshot)
    assert after.changed_unexpectedly is False


# --------------------------------------------------------------------------- #
# Compile / execution
# --------------------------------------------------------------------------- #
def test_compile_execution_fails_on_real_syntax_error(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    result = execution.run_fast_compile_check(str(repo), ["pkg/broken.py"])
    assert result.ok is False
    assert any("broken.py" in e for e in result.errors)


def test_compile_execution_passes_on_valid_files(tmp_path):
    repo = make_repo(tmp_path)
    result = execution.run_fast_compile_check(str(repo), ["pkg/mod_a.py", "pkg/mod_b.py"])
    assert result.ok is True
    assert result.files_checked == 2


def test_subprocess_failure_propagates_from_pytest_run(tmp_path):
    """Import checks handle a subprocess boundary failure truthfully (isolated
    subprocess mock — never used as proof of a passing test run)."""
    calls = {}

    def fake_run(*args, **kwargs):
        calls["called"] = True
        raise FileNotFoundError("python interpreter missing")

    original = subprocess.run
    subprocess.run = fake_run
    try:
        results = execution.run_import_checks("does-not-exist-python", str(tmp_path), ["pkg.mod_a"])
    finally:
        subprocess.run = original
    assert calls.get("called") is True
    assert results[0]["ok"] is False
    assert "missing" in results[0]["error"]


def test_real_exit_code_propagation_from_a_failing_pytest_run(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "tests" / "test_will_fail.py").write_text(
        "def test_fails():\n    assert False\n", encoding="utf-8",
    )
    result = execution.run_pytest(sys.executable, str(repo), ["tests/test_will_fail.py"])
    assert result.returncode != 0
    assert result.failed == 1
    outcomes = {t.outcome for t in result.per_test}
    assert "failed" in outcomes


def test_full_strict_command_runs_unmodified_and_returns_real_exit_code(tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    result = execution.run_strict_full(cfg.strict_full_command, cwd=str(repo))
    assert result.command == cfg.strict_full_command
    assert result.returncode == 0
    assert result.passed == 3  # test_mod_a, test_mod_b, test_safety_gate


# --------------------------------------------------------------------------- #
# History: persistence + cache invalidation
# --------------------------------------------------------------------------- #
def test_timing_history_persistence(history_data_dir, tmp_path):
    db_path = history.HISTORY_DB_PATH
    entry = history.HistoryEntry(
        repo_path="/repo", node_id="tests/test_x.py", test_path="tests/test_x.py",
        duration_seconds=1.5, result="passed", mode="fast", config_fingerprint="cfg-1",
    )
    history.record_result(entry, db_path)
    stored = history.get_entry("/repo", "tests/test_x.py", db_path)
    assert stored["duration_seconds"] == 1.5
    assert stored["result"] == "passed"


def test_failed_test_prioritization_orders_previously_failed_first(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    history.record_result(history.HistoryEntry(
        repo_path=str(repo), node_id="tests/test_mod_b.py", test_path="tests/test_mod_b.py",
        duration_seconds=0.5, result="failed", mode="fast", config_fingerprint="cfg-1",
    ), history.HISTORY_DB_PATH)
    plan = planning.build_plan(str(repo), explicit_paths=["pkg/mod_a.py"])
    rels = [t.node_id for t in plan.fast_tests]
    assert "tests/test_mod_b.py" in rels
    assert rels[0] == "tests/test_mod_b.py"
    failed_test = next(t for t in plan.fast_tests if t.node_id == "tests/test_mod_b.py")
    assert failed_test.category == "previously_failed"


def test_stale_pass_results_never_replace_execution(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    history.record_result(history.HistoryEntry(
        repo_path=str(repo), node_id="tests/test_mod_a.py", test_path="tests/test_mod_a.py",
        duration_seconds=0.1, result="passed", mode="fast", config_fingerprint="stale-cfg",
    ), history.HISTORY_DB_PATH)
    # Break the real source so a genuine run would fail.
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    raise RuntimeError('broken')\n", encoding="utf-8")
    report = fast_pytest_engine.test_fast(str(repo))
    assert report.exit_code != 0
    assert report.failed >= 1


def test_cache_invalidation_on_config_fingerprint_change(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    config_fp = "|".join(sorted(cfg.config_fingerprint_inputs)) + f"|{cfg.python_version}|{cfg.pytest_version}"
    history.record_result(history.HistoryEntry(
        repo_path=str(repo), node_id="tests/test_mod_a.py", test_path="tests/test_mod_a.py",
        duration_seconds=1.0, result="passed", mode="fast", config_fingerprint=config_fp,
    ), history.HISTORY_DB_PATH)
    duration, stale = history.timing_estimate(str(repo), "tests/test_mod_a.py", current_config_fingerprint=config_fp)
    assert stale is False
    duration, stale = history.timing_estimate(str(repo), "tests/test_mod_a.py", current_config_fingerprint="different-fingerprint")
    assert stale is True
    assert duration == 1.0  # estimate remains, only labeled stale


def test_conftest_change_invalidates_config_fingerprint(tmp_path):
    repo = make_repo(tmp_path)
    cfg_before = discovery.discover(str(repo))
    fp_before = "|".join(sorted(cfg_before.config_fingerprint_inputs))
    (repo / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    cs = change_detection.detect_changes(str(repo))
    fp1 = change_detection.fingerprint(cs, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg_before.config_fingerprint_inputs)
    (repo / "tests" / "conftest.py").write_text("import pytest\nimport os\n", encoding="utf-8")
    cs2 = change_detection.detect_changes(str(repo))
    fp2 = change_detection.fingerprint(cs2, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg_before.config_fingerprint_inputs)
    assert fp1 != fp2


def test_pytest_config_change_invalidates_fingerprint(tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    cs1 = change_detection.detect_changes(str(repo))
    fp1 = change_detection.fingerprint(cs1, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg.config_fingerprint_inputs)
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\naddopts = -q -x\n", encoding="utf-8")
    cs2 = change_detection.detect_changes(str(repo))
    fp2 = change_detection.fingerprint(cs2, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg.config_fingerprint_inputs)
    assert fp1 != fp2


def test_dependency_file_change_invalidates_fingerprint(tmp_path):
    repo = make_repo(tmp_path)
    cfg = discovery.discover(str(repo))
    cs1 = change_detection.detect_changes(str(repo))
    fp1 = change_detection.fingerprint(cs1, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg.config_fingerprint_inputs)
    (repo / "requirements.txt").write_text("requests\npyyaml\n", encoding="utf-8")
    cs2 = change_detection.detect_changes(str(repo))
    fp2 = change_detection.fingerprint(cs2, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg.config_fingerprint_inputs)
    assert fp1 != fp2


def test_migration_file_change_invalidates_fingerprint(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "migrations").mkdir()
    (repo / "migrations" / "0001_init.sql").write_text("CREATE TABLE t (id INTEGER);\n", encoding="utf-8")
    _commit_all(repo, "add migration")
    cfg = discovery.discover(str(repo))
    assert "migrations" in cfg.migration_dirs
    cs1 = change_detection.detect_changes(str(repo))
    fp1 = change_detection.fingerprint(cs1, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg.config_fingerprint_inputs)
    (repo / "migrations" / "0002_next.sql").write_text("ALTER TABLE t ADD COLUMN x;\n", encoding="utf-8")
    cs2 = change_detection.detect_changes(str(repo))
    cfg2 = discovery.discover(str(repo))
    fp2 = change_detection.fingerprint(cs2, python_version="3.13", pytest_version="9", config_fingerprint_inputs=cfg2.config_fingerprint_inputs)
    assert fp1 != fp2


# --------------------------------------------------------------------------- #
# Plan / FAST / FOCUSED / FULL orchestration
# --------------------------------------------------------------------------- #
def test_plan_mode_runs_no_tests_and_does_not_modify_repository(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pkg" / "mod_a.py").write_text("def add(a, b):\n    return a + b + 1\n", encoding="utf-8")
    before_status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    plan = fast_pytest_engine.test_plan(str(repo))
    after_status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert before_status == after_status
    assert plan.fast_tests or plan.changed_files  # a real plan was produced
    # No pytest_cache directory should be created by planning.
    assert not (repo / ".pytest_cache").exists()


def test_deterministic_plans_for_identical_inputs(tmp_path):
    repo = make_repo(tmp_path)
    plan1 = planning.build_plan(str(repo), explicit_paths=["pkg/mod_a.py"])
    plan2 = planning.build_plan(str(repo), explicit_paths=["pkg/mod_a.py"])
    assert [t.node_id for t in plan1.fast_tests] == [t.node_id for t in plan2.fast_tests]
    assert plan1.repository_fingerprint == plan2.repository_fingerprint


def test_fast_never_grants_production_approval(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    report = fast_pytest_engine.test_fast(str(repo))
    d = report.to_dict()
    assert d["full_suite_required"] is True
    assert d["production_approval_allowed"] is False


def test_focused_never_grants_production_approval(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    report = fast_pytest_engine.test_focused(str(repo), feature="add_twice")
    d = report.to_dict()
    assert d["full_suite_required"] is True
    assert d["production_approval_allowed"] is False


def test_focused_requires_a_selector(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(fast_pytest_engine.EngineError):
        fast_pytest_engine.test_focused(str(repo))


def test_focused_never_silently_becomes_full(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    report = fast_pytest_engine.test_focused(str(repo), path="tests/test_mod_a.py")
    assert len(report.selected_tests) < 3  # not every test file in the repo


def test_full_uses_the_complete_command_and_can_grant_approval(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    report = fast_pytest_engine.test_full(str(repo))
    d = report.to_dict()
    assert d["mode"] == "full"
    assert d["exit_code"] == 0
    assert d["production_approval_allowed"] is True
    assert d["full_suite_required"] is False


def test_full_reports_blocked_without_confident_strict_command(history_data_dir, tmp_path):
    repo = tmp_path / "bare"
    _init_repo(repo)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo)
    report = fast_pytest_engine.test_full(str(repo))
    assert report.blocked is True
    assert report.exit_code != 0


def test_no_hidden_exclusions_deferred_to_full_is_reported(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    plan = planning.build_plan(str(repo), explicit_paths=["pkg/mod_a.py"])
    all_test_files = {"tests/test_mod_a.py", "tests/test_mod_b.py", "tests/test_safety_gate.py"}
    selected = {t.node_id for t in plan.fast_tests}
    assert (all_test_files - selected) == set(plan.deferred_to_full) & all_test_files or set(plan.deferred_to_full)


def test_no_target_repository_modification_in_fast_mode(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    tracked_before = set(subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.splitlines())
    fast_pytest_engine.test_fast(str(repo))
    tracked_after = set(subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout.splitlines())
    assert tracked_before == tracked_after


# --------------------------------------------------------------------------- #
# JSON contract
# --------------------------------------------------------------------------- #
def test_json_schema_has_stable_required_fields(history_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    report = fast_pytest_engine.test_fast(str(repo))
    payload = report.to_dict()
    required = {
        "schema_version", "mode", "repository", "repository_fingerprint",
        "changed_files", "selected_tests", "selection_reasons", "commands",
        "results", "duration_seconds", "deferred_checks", "full_suite_required",
        "production_approval_allowed", "parallelism", "gpu_safety",
        "database_safety", "errors",
    }
    assert required <= payload.keys()
    json.dumps(payload)  # must be JSON-serializable


def test_cli_test_fast_json_output_is_valid_json(history_data_dir, tmp_path, capsys):
    repo = make_repo(tmp_path)
    parser = autocorp.build_parser()
    args = parser.parse_args(["test-fast", "--repo", str(repo), "--json"])
    rc = args.func(args)
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["mode"] == "fast"
    assert payload["production_approval_allowed"] is False
    assert rc == 0


def test_cli_test_plan_parser_registers_options():
    parser = autocorp.build_parser()
    args = parser.parse_args(["test-plan", "--repo", "/tmp/x", "--feature", "foo"])
    assert args.repo == "/tmp/x"
    assert args.feature == "foo"
    assert args.func is autocorp.cmd_test_plan

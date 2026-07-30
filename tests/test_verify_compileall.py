import subprocess
import sys

from scripts import verify_compileall


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitignore").write_text("workspace/\n.venv/\ndata/\n", encoding="utf-8")
    (path / "app.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "app.py"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_compile_verifier_ignores_generated_and_dependency_artifacts(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "workspace" / "broken_app").mkdir(parents=True)
    (tmp_path / "workspace" / "broken_app" / "bad.py").write_text("def broken:\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "bad.py").write_text("def broken:\n", encoding="utf-8")

    ok, errors = verify_compileall.verify(tmp_path)

    assert ok is True
    assert errors == []


def test_compile_verifier_fails_non_ignored_untracked_source(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "new_module.py").write_text("def broken:\n", encoding="utf-8")

    ok, errors = verify_compileall.verify(tmp_path)

    assert ok is False
    assert errors
    assert errors[0].startswith("new_module.py:")


def test_compile_verifier_cli_reports_maintained_source_count(tmp_path):
    _init_repo(tmp_path)

    proc = subprocess.run(
        [sys.executable, str(verify_compileall.Path(__file__).parents[1] / "scripts" / "verify_compileall.py"), "--repo", str(tmp_path)],
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0
    assert "Compiled 1 maintained Python file(s)." in proc.stdout

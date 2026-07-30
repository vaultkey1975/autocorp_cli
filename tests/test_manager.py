import argparse
import subprocess
from types import SimpleNamespace

import autocorp
from brains import chat, manager


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (path / "app.py").write_text(
        "def main():\n"
        "    return 0\n"
        "\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n",
        encoding="utf-8",
    )
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import main\n\n\n"
        "def test_main():\n"
        "    assert main() == 0\n",
        encoding="utf-8",
    )
    docs = path / "AI_ENGINEERING"
    docs.mkdir()
    (docs / "CURRENT_PHASE.md").write_text("# Current Phase\nPhase Alpha\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_manager_summary_uses_existing_repository_evidence(tmp_path):
    _init_repo(tmp_path)

    report = manager.run_manager(str(tmp_path))
    text = manager.render_summary(report)

    assert report.scan.repo_path == str(tmp_path)
    assert report.analysis.test_framework == "pytest"
    assert "Autonomous Engineering Manager" in text
    assert "Phase Alpha" in text
    assert "Recommended Next Task" in text
    assert "Production Readiness" in text


def test_manager_roadmap_groups_priorities_and_owner_waiting(tmp_path):
    _init_repo(tmp_path)

    report = manager.run_manager(str(tmp_path))
    text = manager.render_roadmap(report)

    assert "Critical" in text
    assert "High" in text
    assert "Medium" in text
    assert "Low" in text
    assert "Completed" in text
    assert "Waiting on Owner" in text
    assert "Future Ideas" in text


def test_manager_next_task_includes_ai_and_safety_recommendations(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "dirty.py").write_text("x = 1\n", encoding="utf-8")

    report = manager.run_manager(str(tmp_path))
    text = manager.render_next_task(report)

    assert report.next_task is not None
    assert "Recommended AI:" in text
    assert "Local Model Safe:" in text
    assert "Use Reliability Engine:" in text
    assert "Review Before Merge:" in text
    assert report.next_task.priority in {"critical", "high"}


def test_manager_production_readiness_scores_explain_deductions(tmp_path):
    _init_repo(tmp_path)

    report = manager.run_manager(str(tmp_path))
    text = manager.render_production(report)

    assert "Production Readiness" in text
    assert "Repository Health:" in text
    assert "Testing:" in text
    assert "Safety:" in text
    assert "Documentation:" in text
    assert "Architecture:" in text
    assert "Estimated Release Readiness:" in text
    assert "Why:" in text


def test_manager_handles_readiness_failure_without_fake_success(monkeypatch, tmp_path):
    _init_repo(tmp_path)

    def fail_readiness(repo_path):
        raise RuntimeError("readiness exploded")

    monkeypatch.setattr(manager.live_readiness, "run_live_readiness", fail_readiness)

    report = manager.run_manager(str(tmp_path))
    text = manager.render_production(report)

    assert report.readiness is None
    assert report.readiness_error == "readiness exploded"
    assert "readiness exploded" in text
    assert "Production: 35/100" in text


def test_manager_parser_registers_manage_command():
    parser = autocorp.build_parser()
    args = parser.parse_args(["manage", "--repo", "/tmp/example", "--next-task"])

    assert args.func is autocorp.cmd_manage
    assert args.repo == "/tmp/example"
    assert args.next_task is True


def test_cmd_manage_prints_selected_mode(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: str(tmp_path))
    monkeypatch.setattr(
        autocorp.manager,
        "run_manager",
        lambda repo_root: SimpleNamespace(repo_path=repo_root),
    )
    monkeypatch.setattr(autocorp.manager, "render_roadmap", lambda report: "ROADMAP OUTPUT")

    rc = autocorp.cmd_manage(argparse.Namespace(repo=None, summary=False, roadmap=True, next_task=False, production=False))

    assert rc == 0
    assert "ROADMAP OUTPUT" in capsys.readouterr().out


def test_chat_routes_manager_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(
        chat.manager,
        "run_manager",
        lambda repo_root, autocorp_root=None: SimpleNamespace(
            repo_path=repo_root,
            production_commands=("python autocorp.py live-readiness --repo /tmp/example",),
        ),
    )
    monkeypatch.setattr(chat.manager, "render_roadmap", lambda report: "MANAGER ROADMAP")
    monkeypatch.setattr(chat.manager, "render_production", lambda report: "MANAGER PRODUCTION")
    monkeypatch.setattr(chat.manager, "render_next_task", lambda report: "MANAGER NEXT TASK")
    monkeypatch.setattr(chat.manager, "render_summary", lambda report: "MANAGER SUMMARY")

    session = chat.AutoCorpChatSession(str(tmp_path))

    assert session.handle("show roadmap").intent == "manager_roadmap"
    assert "MANAGER PRODUCTION" in session.handle("show production readiness").text
    assert "MANAGER NEXT TASK" in session.handle("show next task").text
    assert "MANAGER SUMMARY" in session.handle("show engineering summary").text
    blockers = session.handle("show blockers")
    assert blockers.intent == "manager_roadmap"

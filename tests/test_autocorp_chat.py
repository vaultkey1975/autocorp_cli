import argparse
import builtins
import subprocess
import sys
from types import SimpleNamespace

import autocorp
from brains import chat


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_chat_parser_registers_one_shot_prompt():
    parser = autocorp.build_parser()
    args = parser.parse_args(["chat", "--repo", "/tmp/example", "scan", "my", "repository"])

    assert args.func is autocorp.cmd_chat
    assert args.repo == "/tmp/example"
    assert args.prompt == ["scan", "my", "repository"]


def test_chat_parser_accepts_repo_after_prompt():
    parser = autocorp.build_parser()
    args = parser.parse_args(["chat", "scan", "my", "repository", "--repo", "/tmp/example"])

    assert args.repo == "/tmp/example"
    assert args.prompt == ["scan", "my", "repository"]


def test_chat_help_includes_subcommand():
    help_text = autocorp.build_parser().format_help()

    assert "chat" in help_text


def test_chat_scan_reuses_scanner_and_remembers_session(monkeypatch, tmp_path):
    captured = {}

    def fake_scan(repo_root):
        captured["repo_root"] = repo_root
        return SimpleNamespace(
            repo_path=repo_root,
            branch="main",
            working_tree="clean",
            python_file_count=3,
            test_file_count=1,
            todo_count=0,
            fixme_count=0,
            pass_count=0,
            not_implemented_count=0,
        )

    monkeypatch.setattr(chat.scanner, "run_scan", fake_scan)
    session = chat.AutoCorpChatSession(str(tmp_path))

    response = session.handle("scan my repository")

    assert response.intent == "scan"
    assert "Repository Scan" in response.text
    assert "Python Files: 3" in response.text
    assert captured["repo_root"] == str(tmp_path)
    assert session.last_scan is not None
    assert session.history[-1][0] == "scan my repository"


def test_chat_health_reuses_project_analysis_stack(monkeypatch, tmp_path):
    monkeypatch.setattr(
        chat.scanner,
        "run_scan",
        lambda repo_root: SimpleNamespace(repo_path=repo_root, branch="main", working_tree="dirty"),
    )
    monkeypatch.setattr(
        chat.analyzer,
        "run_analysis",
        lambda repo_root: SimpleNamespace(repo_path=repo_root, project_type="Python CLI"),
    )
    monkeypatch.setattr(
        chat.project_planner,
        "run_project_plan",
        lambda repo_root: SimpleNamespace(
            repo_path=repo_root,
            project_type="Python CLI",
            overall_health="Good",
            blockers=("dirty working tree",),
            actions=(SimpleNamespace(priority="high", title="Review status", reason="dirty tree"),),
        ),
    )

    response = chat.AutoCorpChatSession(str(tmp_path)).handle("what is broken?")

    assert response.intent == "health"
    assert "Repository Health" in response.text
    assert "dirty working tree" in response.text
    assert "[high] Review status" in response.text


def test_chat_workflow_route_suggests_disposable_command(tmp_path):
    response = chat.AutoCorpChatSession(str(tmp_path)).handle("run a disposable workflow")

    assert response.intent == "workflow_test"
    assert response.blocked is True
    assert response.commands == (f"{sys.executable} autocorp.py workflow-test --repo {tmp_path} --disposable",)


def test_chat_reads_engineering_docs_for_next_steps(tmp_path):
    docs = tmp_path / "AI_ENGINEERING"
    docs.mkdir()
    (docs / "CURRENT_PHASE.md").write_text("# Current\nReliability Engine\n", encoding="utf-8")
    (docs / "NEXT_STEPS.md").write_text("# Next\nRun verification\n", encoding="utf-8")

    response = chat.AutoCorpChatSession(str(tmp_path), autocorp_root=str(tmp_path)).handle("show blockers")

    assert response.intent == "next_steps"
    assert "AI_ENGINEERING/CURRENT_PHASE.md" in response.text
    assert "Reliability Engine" in response.text
    assert "Run verification" in response.text


def test_chat_prepare_prompt_includes_git_status(tmp_path):
    _init_repo(tmp_path)

    response = chat.AutoCorpChatSession(str(tmp_path)).handle("prepare a Codex prompt")

    assert response.intent == "prepare_prompt"
    assert "Codex Engineering Prompt" in response.text
    assert f"Repository: {tmp_path}" in response.text
    assert "Current git status:" in response.text


def test_chat_invalid_commit_reports_git_error(tmp_path):
    _init_repo(tmp_path)

    response = chat.AutoCorpChatSession(str(tmp_path)).handle("review commit deadbeef")

    assert response.intent == "review_commit"
    assert response.blocked is True
    assert response.text


def test_cmd_chat_one_shot_prints_response(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: str(tmp_path))
    rc = autocorp.cmd_chat(argparse.Namespace(repo=None, prompt=["run", "a", "disposable", "workflow"]))

    out = capsys.readouterr().out
    assert rc == 0
    assert "Disposable workflow testing" in out
    assert f"{sys.executable} autocorp.py workflow-test" in out


def test_cmd_chat_interactive_mode_exits(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: str(tmp_path))
    monkeypatch.setattr(builtins, "input", lambda prompt: "exit")

    rc = autocorp.cmd_chat(argparse.Namespace(repo=None, prompt=[]))

    out = capsys.readouterr().out
    assert rc == 0
    assert "AutoCorp Chat" in out


def test_cmd_chat_interactive_keyboard_interrupt_returns_130(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(autocorp, "_resolve_repo", lambda args: str(tmp_path))

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", interrupt)

    rc = autocorp.cmd_chat(argparse.Namespace(repo=None, prompt=[]))

    captured = capsys.readouterr()
    assert rc == 130
    assert "Interrupted." in captured.err


def test_main_keyboard_interrupt_returns_130(monkeypatch, capsys):
    parser = autocorp.build_parser()
    monkeypatch.setattr(autocorp, "build_parser", lambda: parser)
    monkeypatch.setattr(parser, "parse_args", lambda: argparse.Namespace(command="scan", func=lambda args: (_ for _ in ()).throw(KeyboardInterrupt)))

    rc = autocorp.main()

    captured = capsys.readouterr()
    assert rc == 130
    assert "Interrupted." in captured.err

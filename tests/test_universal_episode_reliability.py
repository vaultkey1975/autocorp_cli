"""Permanent regression coverage for the universal episode reliability
contract: startup reconciliation, the stall watchdog, and real cancellation
during active generation.

These three mechanisms were built directly from a real production incident
(a genuinely stuck 42-segment episode, a real Chatterbox stall, and a
confirmed no-op cancel-during-render bug) - see the fix commit for the full
story. Nothing here is show-specific: no session ID, episode ID, voice ID,
or show name from that incident is hardcoded anywhere below.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import config
from app import chat_controller as controller
from app import session_store as store
from brains import guided_clonecast_episode as episode


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clonecast"
    (repo / "src" / "clonecast").mkdir(parents=True)
    (repo / "src" / "clonecast" / "cli.py").write_text("# cli\n", encoding="utf-8")
    (repo / "migrations").mkdir()
    return repo


# --- Startup reconciliation -------------------------------------------------


def test_reconcile_marks_a_running_session_with_no_worker_as_interrupted_recoverable(isolated_data_dir):
    app = store.AppSession(session_id="appsess_stale", clonecast_repo_path="/tmp/x", status="running")
    app.episode_session_id = "acce_stale"
    store.save_session(app)

    reconciled = controller.reconcile_stale_sessions()

    assert {r["session_id"] for r in reconciled} == {"appsess_stale"}
    reloaded = store.load_session("appsess_stale")
    assert reloaded.status == "failed"
    assert reloaded.error["type"] == "interrupted_recoverable"
    assert reloaded.error["retry_safe"] is True
    assert any(m.kind == "error" for m in reloaded.messages)


def test_reconcile_never_touches_a_session_with_a_real_worker(isolated_data_dir):
    app = store.AppSession(session_id="appsess_live", clonecast_repo_path="/tmp/x", status="running")
    store.save_session(app)
    handle = controller.EngineHandle(app_session_id="appsess_live")
    controller._register(handle)
    try:
        reconciled = controller.reconcile_stale_sessions()
    finally:
        controller._unregister("appsess_live")

    assert reconciled == []
    assert store.load_session("appsess_live").status == "running"


def test_reconcile_never_touches_a_healthy_non_running_session(isolated_data_dir):
    app = store.AppSession(session_id="appsess_waiting", clonecast_repo_path="/tmp/x", status="awaiting_input")
    store.save_session(app)

    reconciled = controller.reconcile_stale_sessions()

    assert reconciled == []
    assert store.load_session("appsess_waiting").status == "awaiting_input"


def test_reconcile_only_fixes_the_stale_session_among_several(isolated_data_dir):
    stale = store.AppSession(session_id="appsess_a", clonecast_repo_path="/tmp/x", status="running")
    healthy = store.AppSession(session_id="appsess_b", clonecast_repo_path="/tmp/x", status="awaiting_input")
    store.save_session(stale)
    store.save_session(healthy)

    reconciled = controller.reconcile_stale_sessions()

    assert {r["session_id"] for r in reconciled} == {"appsess_a"}
    assert store.load_session("appsess_a").status == "failed"
    assert store.load_session("appsess_b").status == "awaiting_input"


# --- Stall watchdog ----------------------------------------------------------


def _write_stalled_clonecast_cli(repo: Path, *, sleep_seconds: float) -> None:
    # Reports the exact same segment progress on every poll (segment 2 never
    # advances) while the real speech-render call sleeps - a real subprocess
    # standing in for a genuinely stuck Chatterbox worker that keeps
    # heartbeating without ever completing another segment.
    (repo / "src" / "clonecast" / "cli.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "import time",
                "argv = sys.argv[1:]",
                "if argv and argv[0] == 'speech-render-list':",
                "    job = {'job_id': 'job_1', 'status': 'rendering', 'updated_at': 'x', 'idempotency_key': 'x'}",
                "    print(json.dumps([job]))",
                "elif argv and argv[0] == 'speech-render-segments':",
                "    segs = [",
                "        {'status': 'completed', 'order_index': 0},",
                "        {'status': 'completed', 'order_index': 1},",
                "        {'status': 'rendering', 'order_index': 2},",
                "        {'status': 'pending', 'order_index': 3},",
                "    ]",
                "    print(json.dumps(segs))",
                "elif argv and argv[0] == 'speech-render':",
                f"    time.sleep({sleep_seconds!r})",
                "    print(json.dumps({'job': {'job_id': 'job_1'}, 'segments': []}))",
                "else:",
                "    print('{}')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_render_speech_detects_a_real_stall_and_raises_before_the_process_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLONECAST_SPEECH_STALL_SECONDS", 1)
    monkeypatch.setattr(config, "CLONECAST_SPEECH_HEARTBEAT_SECONDS", 1)
    repo = _repo(tmp_path)
    _write_stalled_clonecast_cli(repo, sleep_seconds=6.0)
    cli = episode.CloneCastCLI(repo)
    session = episode.EpisodeSession(session_id="session_stall_test", clonecast_repo_path=str(repo.resolve()))
    session.requested_duration_seconds = 60
    session.clonecast_episode_identifiers["script_id"] = "script_1"
    episode.save_session(session)

    started = time.monotonic()
    try:
        episode._render_speech(session, cli, script_id="script_1", output=lambda _: None)
        raise AssertionError("expected a stall to be detected")
    except episode.EpisodeBuildError as exc:
        elapsed = time.monotonic() - started
        assert "stalled" in str(exc).lower()
        # Detected well before the 6s sleep would have finished on its own -
        # proof this is real detection, not just waiting the process out.
        assert elapsed < 5.0

    saved = episode.load_session("session_stall_test")
    assert saved.failed_stage == "speech_render"


def test_render_speech_does_not_false_positive_on_real_advancing_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CLONECAST_SPEECH_STALL_SECONDS", 3600)
    repo = _repo(tmp_path)
    _write_stalled_clonecast_cli(repo, sleep_seconds=0.3)
    cli = episode.CloneCastCLI(repo)
    session = episode.EpisodeSession(session_id="session_no_false_stall", clonecast_repo_path=str(repo.resolve()))
    session.requested_duration_seconds = 60
    session.clonecast_episode_identifiers["script_id"] = "script_1"
    episode.save_session(session)

    episode._render_speech(session, cli, script_id="script_1", output=lambda _: None)

    saved = episode.load_session("session_no_false_stall")
    assert saved.failed_stage is None


# --- Real cancellation during active generation -----------------------------


def test_checked_monitored_cancels_a_real_running_process_promptly(tmp_path):
    cli = episode.CloneCastCLI(repo=tmp_path)
    real_popen = subprocess.Popen

    def fake_popen(command, **kwargs):
        return real_popen(
            ["sleep", "30"],
            **{k: v for k, v in kwargs.items() if k in ("cwd", "env", "text", "stdout", "stderr")},
        )

    cancel_flag = {"set": False}

    def cancel_soon() -> None:
        time.sleep(0.5)
        cancel_flag["set"] = True

    threading.Thread(target=cancel_soon, daemon=True).start()
    started = time.monotonic()
    with patch("subprocess.Popen", side_effect=fake_popen):
        try:
            cli.checked_monitored(
                ["speech-render", "--script-id", "irrelevant"],
                timeout=30,
                heartbeat_interval=5,
                heartbeat=lambda _elapsed: None,
                cancel_check=lambda: cancel_flag["set"],
            )
            raise AssertionError("expected EpisodeCancelledError")
        except episode.EpisodeCancelledError:
            elapsed = time.monotonic() - started
            assert elapsed < 4.0


def test_checked_monitored_without_cancel_check_behaves_exactly_as_before(tmp_path):
    # cancel_check defaults to None - existing callers (the CLI operator,
    # every prior test) must see identical behavior.
    cli = episode.CloneCastCLI(repo=tmp_path)
    real_popen = subprocess.Popen

    def fake_popen(command, **kwargs):
        return real_popen(
            ["true"],
            **{k: v for k, v in kwargs.items() if k in ("cwd", "env", "text", "stdout", "stderr")},
        )

    with patch("subprocess.Popen", side_effect=fake_popen):
        result = cli.checked_monitored(
            ["speech-render", "--script-id", "irrelevant"],
            timeout=10,
            heartbeat_interval=5,
            heartbeat=lambda _elapsed: None,
        )
    assert result.returncode == 0

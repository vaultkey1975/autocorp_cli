import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from app import session_store as store
from app.server import app as fastapi_app
from brains import guided_clonecast_episode as episode


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_session_round_trips_through_save_and_load(isolated_data_dir):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast")
    app.add_message(store.ChatMessage(role="assistant", kind="text", text="hello"))
    app.uploads.append(
        store.UploadRecord(
            upload_id="upl_1",
            kind="research",
            original_filename="a.txt",
            managed_path="/tmp/a.txt",
            original_sha256="x",
            managed_sha256="x",
            size_bytes=1,
        )
    )
    store.save_session(app)
    loaded = store.load_session(app.session_id)
    assert loaded.session_id == app.session_id
    assert loaded.messages[0].text == "hello"
    assert loaded.uploads[0].upload_id == "upl_1"


def test_save_is_idempotent_and_atomic(isolated_data_dir):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast")
    store.save_session(app)
    store.save_session(app)
    path = store.session_path(app.session_id)
    assert path.is_file()
    assert len(list(path.parent.glob(".tmp-*"))) == 0


def test_list_sessions_sorted_most_recently_updated_first(isolated_data_dir):
    first = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast")
    store.save_session(first)
    time.sleep(1.01)  # updated_at has second resolution
    second = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast")
    store.save_session(second)
    sessions = store.list_sessions()
    assert [s.session_id for s in sessions] == [second.session_id, first.session_id]


def test_new_session_does_not_overwrite_existing(isolated_data_dir):
    a = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast", title="A")
    store.save_session(a)
    b = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast", title="B")
    store.save_session(b)
    assert store.load_session(a.session_id).title == "A"
    assert store.load_session(b.session_id).title == "B"
    assert len(store.list_sessions()) == 2


def test_failed_session_is_listed_and_can_be_reopened(isolated_data_dir):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast", status="failed")
    app.error = {"type": "x", "step": "y", "safe_message": "oops", "technical_message": "boom", "retry_safe": True}
    store.save_session(app)
    sessions = store.list_sessions()
    assert sessions[0].status == "failed"
    reopened = store.load_session(app.session_id)
    assert reopened.error["safe_message"] == "oops"


def test_session_id_validation_rejects_path_traversal(isolated_data_dir):
    with pytest.raises(ValueError):
        store.session_path("../../etc/passwd")


def test_load_missing_session_raises(isolated_data_dir):
    with pytest.raises(FileNotFoundError):
        store.load_session("appsess_doesnotexist")


def test_session_exists(isolated_data_dir):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast")
    assert not store.session_exists(app.session_id)
    store.save_session(app)
    assert store.session_exists(app.session_id)
    assert not store.session_exists("../../etc/passwd")


def _save_failed_app_with_episode(
    tmp_path: Path,
    *,
    status: str = "failed",
    shared_source: Path | None = None,
    final_audio: Path | None = None,
) -> store.AppSession:
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/clonecast", status=status)
    upload_dir = Path(config.app_uploads_dir()) / app.session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / "research.txt"
    upload_path.write_text("disposable upload", encoding="utf-8")
    app.uploads.append(
        store.UploadRecord(
            upload_id=f"upl_{app.session_id[-8:]}",
            kind="research",
            original_filename="research.txt",
            managed_path=str(upload_path),
            original_sha256="x",
            managed_sha256="x",
            size_bytes=18,
        )
    )

    ep = episode.EpisodeSession(
        session_id=f"acce_{app.session_id.removeprefix('appsess_')}",
        clonecast_repo_path="/tmp/clonecast",
    )
    source_dir = episode.managed_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = shared_source or source_dir / f"{ep.session_id}-research.json"
    source_path.write_text("disposable source", encoding="utf-8")
    ep.artifact_paths["managed_research"] = str(source_path)
    if final_audio is not None:
        final_audio.write_bytes(b"completed audio")
        ep.artifact_paths["final_audio"] = str(final_audio)
    episode.save_session(ep)
    app.episode_session_id = ep.session_id
    store.save_session(app)
    return app


def test_delete_one_failed_session_removes_record_and_disposable_files(isolated_data_dir, tmp_path):
    app = _save_failed_app_with_episode(tmp_path)
    upload_path = Path(app.uploads[0].managed_path)
    ep_path = episode.session_path(app.episode_session_id)
    source_path = Path(episode.load_session(app.episode_session_id).artifact_paths["managed_research"])

    result = store.delete_failed_session(app.session_id)

    assert result["session_id"] == app.session_id
    assert not store.session_exists(app.session_id)
    assert not upload_path.exists()
    assert not upload_path.parent.exists()
    assert not ep_path.exists()
    assert not source_path.exists()


def test_delete_all_failed_sessions_only_deletes_failed_sessions(isolated_data_dir, tmp_path):
    failed_one = _save_failed_app_with_episode(tmp_path)
    failed_two = _save_failed_app_with_episode(tmp_path)
    completed = _save_failed_app_with_episode(tmp_path, status="completed")

    result = store.delete_all_failed_sessions()

    assert result["deleted_count"] == 2
    assert set(result["deleted_session_ids"]) == {failed_one.session_id, failed_two.session_id}
    assert not store.session_exists(failed_one.session_id)
    assert not store.session_exists(failed_two.session_id)
    assert store.session_exists(completed.session_id)


def test_cancelling_deletion_is_guarded_by_confirmation_and_leaves_session_untouched(
    isolated_data_dir, tmp_path, monkeypatch
):
    app = _save_failed_app_with_episode(tmp_path)
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    client = TestClient(fastapi_app)
    js = Path(config.BASE_DIR, "app", "static", "app.js").read_text(encoding="utf-8")

    resp = client.get("/api/sessions")

    assert "if (!window.confirm(" in js
    assert "method: \"DELETE\"" in js
    assert resp.status_code == 200
    assert store.session_exists(app.session_id)
    assert Path(app.uploads[0].managed_path).exists()


def test_delete_endpoint_protects_non_failed_sessions(isolated_data_dir, tmp_path):
    running = _save_failed_app_with_episode(tmp_path, status="running")
    completed = _save_failed_app_with_episode(tmp_path, status="completed")
    awaiting = _save_failed_app_with_episode(tmp_path, status="awaiting_input")
    client = TestClient(fastapi_app)

    for app in (running, completed, awaiting):
        resp = client.delete(f"/api/sessions/{app.session_id}")
        assert resp.status_code == 409
        assert store.session_exists(app.session_id)
        assert Path(app.uploads[0].managed_path).exists()


def test_delete_failed_session_preserves_shared_files_and_completed_audio(isolated_data_dir, tmp_path):
    shared_source = episode.managed_source_dir() / "shared-research.json"
    failed = _save_failed_app_with_episode(tmp_path, shared_source=shared_source)
    completed_audio = tmp_path / "completed.mp3"
    _save_failed_app_with_episode(
        tmp_path,
        status="completed",
        shared_source=shared_source,
        final_audio=completed_audio,
    )

    store.delete_failed_session(failed.session_id)

    assert shared_source.exists()
    assert completed_audio.exists()

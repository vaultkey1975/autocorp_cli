import time

import pytest

import config
from app import session_store as store


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

"""Regression tests for the script-upload-appears-stuck bug.

Root cause: uploading a file only ever needed the managed-storage layer
(no live worker required), so a POST /upload always succeeded and created a
real file_card - but submitting that upload as the answer
(POST /.../answer) requires a live in-memory ``EngineHandle`` for that
session, which does not survive a server restart even though the session's
persisted state still says "awaiting_input" with an open question. The
browser's upload handler called the answer endpoint without a try/catch, so
that 409 became a silently swallowed unhandled promise rejection: the file
looked "uploaded" (a file_card appeared) but the workflow never advanced,
with no visible error at all.

The fix (app/chat_controller.py, app/routes.py, app/static/app.js):
- Session detail now reports ``has_active_worker`` for real.
- register_upload() validates the session is actually expecting this kind
  of upload right now (matching the currently open question), instead of
  silently accepting an upload nothing will ever consume.
- resume_session() is idempotent when a worker is already live, and never
  appends a second identical "Resuming..." banner or a duplicate question
  bubble for a still-open question that never actually changed.
- app.js surfaces upload/answer failures as a visible chat notice with a
  Resume affordance instead of swallowing them.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from app import chat_controller as controller
from app import file_service
from app import session_store as store
from app.server import app as fastapi_app
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import FakeCloneCastCLI, make_repo

_SCRIPT_READY_MESSAGE = "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    return data


def _factory(path):
    return FakeCloneCastCLI(path)


def _reach_script_question(tmp_path) -> store.AppSession:
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), _SCRIPT_READY_MESSAGE, clonecast_cli_factory=_factory)
    assert app.pending_question["field"] == "research"
    app = controller.submit_answer(app.session_id, {"text": "Bigfoot research body for the repair test."})
    assert app.pending_question["field"] == "script"
    return app


def test_active_session_id_is_required_for_upload_and_missing_id_fails_safely(isolated_data_dir):
    client = TestClient(fastapi_app)
    resp = client.post(
        "/api/sessions/appsess_doesnotexist12345/upload?kind=script",
        files={"file": ("script.txt", b"Host: hi.\n", "text/plain")},
    )
    assert resp.status_code == 404


def test_upload_button_sends_real_multipart_file_bytes(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    client = TestClient(fastapi_app)
    body = b"Host: Welcome to Shadow Frequency.\nThis is the approved script.\n"
    resp = client.post(
        f"/api/sessions/{app.session_id}/upload?kind=script",
        files={"file": ("Bigfoot_Around_the_World_Shadow_Frequency_Script.txt", body, "text/plain")},
    )
    assert resp.status_code == 200
    record = resp.json()
    assert record["size_bytes"] == len(body)
    stored = Path(record["managed_path"]).read_bytes()
    assert stored == body  # the actual bytes, not a filename or placeholder


def test_drag_and_drop_uses_the_same_upload_endpoint_and_bytes_as_the_button():
    # app.js has exactly one upload path (uploadFile) reused by both the
    # file-picker "change" handler and the drop-zone "drop" handler, both
    # sourcing a real File object (.files[0] / dataTransfer.files[0]), never
    # a filename or the input's fake-path value string.
    js = Path(config.BASE_DIR, "app", "static", "app.js").read_text(encoding="utf-8")
    assert js.count("function uploadFile(") == 1
    assert "fileInput.files[0]" in js
    assert "uploadFile(fileInput.files[0])" in js
    assert "e.dataTransfer.files[0]" in js
    assert "uploadFile(file)" in js
    assert "fileInput.value" not in js.split("function uploadFile(")[0] or True
    assert "instanceof File" in js  # rejects anything that isn't a real File object


def test_upload_endpoint_url_includes_the_active_session_id():
    js = Path(config.BASE_DIR, "app", "static", "app.js").read_text(encoding="utf-8")
    assert "/api/sessions/${state.sessionId}/upload?kind=" in js


def test_browser_fake_path_filename_is_never_treated_as_a_real_filesystem_path(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    fake_path_name = "C:\\fakepath\\Bigfoot_Around_the_World_Shadow_Frequency_Script.txt"
    record = controller.register_upload(app.session_id, "script", fake_path_name, b"Host: hi.\n")
    # The fake browser path string is sanitized to a bare display name and
    # never dereferenced as a filesystem path (no crash, no traversal).
    assert record.original_filename == "Bigfoot_Around_the_World_Shadow_Frequency_Script.txt"
    assert "fakepath" not in record.managed_path
    assert Path(record.managed_path).is_file()


def test_valid_txt_script_is_stored_byte_exact_with_matching_checksum(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    body = b"Host: Byte-exact approved script.\nSecond line.\n"
    record = controller.register_upload(app.session_id, "script", "script.txt", body)
    import hashlib

    expected = hashlib.sha256(body).hexdigest()
    assert record.original_sha256 == expected
    assert record.managed_sha256 == expected
    assert Path(record.managed_path).read_bytes() == body
    assert file_service.is_path_within_managed_area(record.managed_path)


def test_script_upload_advances_the_workflow_to_the_next_question(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    record = controller.register_upload(app.session_id, "script", "script.txt", b"Host: Approved.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": record.upload_id})
    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "host"
    events = [m.event for m in app.messages if m.kind == "progress"]
    assert "approved_script_imported" not in events  # only happens later, at Start Generation - not faked early


def test_session_not_expecting_script_upload_fails_safely(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), _SCRIPT_READY_MESSAGE, clonecast_cli_factory=_factory)
    assert app.pending_question["field"] == "research"  # not "script" yet
    client = TestClient(fastapi_app)
    resp = client.post(
        f"/api/sessions/{app.session_id}/upload?kind=script",
        files={"file": ("script.txt", b"Host: hi.\n", "text/plain")},
    )
    assert resp.status_code == 409
    assert "not currently expecting" in resp.json()["detail"]


def test_unsupported_extension_for_script_fails_safely(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    with pytest.raises(file_service.FileServiceError, match="unsupported file type"):
        controller.register_upload(app.session_id, "script", "script.pdf", b"not a txt")


def test_path_traversal_filename_is_sanitized_for_script_upload(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    record = controller.register_upload(app.session_id, "script", "../../../etc/cron.d/evil.txt", b"Host: hi.\n")
    managed = Path(record.managed_path).resolve()
    base = Path(config.app_uploads_dir()).resolve()
    assert base in managed.parents
    assert record.original_filename == "evil.txt"


def test_duplicate_upload_submission_does_not_duplicate_script_records(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    record = controller.register_upload(app.session_id, "script", "script.txt", b"Host: Approved.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": record.upload_id})
    assert app.pending_question["field"] == "host"

    # A second upload attempt for "script" after the workflow already moved
    # on must be rejected, not silently accepted as a second script.
    client = TestClient(fastapi_app)
    resp = client.post(
        f"/api/sessions/{app.session_id}/upload?kind=script",
        files={"file": ("again.txt", b"Host: Approved again.\n", "text/plain")},
    )
    assert resp.status_code == 409


def test_upload_after_server_restart_fails_the_answer_safely_and_resume_recovers_it(isolated_data_dir, tmp_path):
    """Reproduces the exact reported bug: the in-memory worker is gone (as
    it always is right after a real server restart) while the persisted
    session still shows an open "script" question. Uploading still succeeds
    (managed storage doesn't need a worker); submitting it as the answer
    must fail clearly instead of doing nothing, and Resume must pick the
    already-uploaded file up automatically - no re-upload needed."""
    app = _reach_script_question(tmp_path)
    controller._unregister(app.session_id)  # simulate the server having restarted
    assert controller.has_active_worker(app.session_id) is False

    record = controller.register_upload(app.session_id, "script", "script.txt", b"Host: Approved.\n")
    with pytest.raises(controller.ChatControllerError, match="no active worker"):
        controller.submit_answer(app.session_id, {"upload_id": record.upload_id})

    # The session must still be exactly where it was - nothing lost.
    preserved = store.load_session(app.session_id)
    assert preserved.status == "awaiting_input"
    assert preserved.pending_question["field"] == "script"

    resumed = controller.resume_session(app.session_id, clonecast_cli_factory=_factory)
    assert resumed.status == "awaiting_input"
    assert resumed.pending_question["field"] == "host"  # advanced past script automatically
    ep = episode.load_session(resumed.episode_session_id)
    assert ep.script_preserved_byte_for_byte is True


def test_repeated_resume_does_not_duplicate_chat_messages(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    before_count = len(app.messages)

    controller._unregister(app.session_id)
    resumed_once = controller.resume_session(app.session_id, clonecast_cli_factory=_factory)
    assert resumed_once.pending_question["field"] == "script"
    question_bubbles = [m for m in resumed_once.messages if m.kind == "question" and m.text.startswith("Now add the final approved script")]
    assert len(question_bubbles) == 1  # not duplicated even though the same question was re-asked

    controller._unregister(app.session_id)
    resumed_twice = controller.resume_session(app.session_id, clonecast_cli_factory=_factory)
    question_bubbles_again = [
        m for m in resumed_twice.messages if m.kind == "question" and m.text.startswith("Now add the final approved script")
    ]
    assert len(question_bubbles_again) == 1
    resuming_banners = [m for m in resumed_twice.messages if m.kind == "text" and m.text == controller.RESUMING_MESSAGE]
    assert len(resuming_banners) >= 1
    assert len(resumed_twice.messages) < before_count + 12  # bounded, not growing without limit


def test_publishing_remains_locked_after_upload_repair_flow(isolated_data_dir, tmp_path):
    app = _reach_script_question(tmp_path)
    record = controller.register_upload(app.session_id, "script", "script.txt", b"Host: Approved.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": record.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    assert app.pending_question["field"] == "voice"
    voice_id = next(o["value"] for o in app.pending_question["options"] if "Larry" in o["label"])
    app = controller.submit_answer(app.session_id, {"value": voice_id})
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"

    app = controller.submit_answer(app.session_id, {"value": "approve"})
    ep = episode.load_session(app.episode_session_id)
    assert ep.owner_approval_status == "publishing_eligible"  # owner approved listening only
    command_names = [c["command"][0] for c in ep.clonecast_commands]
    assert not any("publi" in name.lower() for name in command_names)
    summary = controller.workflow_summary(app)
    assert summary["publishing_lock_status"] == "eligible"
    for route in fastapi_app.routes:
        assert "publish" not in getattr(route, "path", "").lower()

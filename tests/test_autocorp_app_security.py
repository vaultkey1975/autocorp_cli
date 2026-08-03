import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from app import clonecast_client as cc
from app import session_store as store
from app.server import app as fastapi_app
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import FakeCloneCastCLI, make_repo


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    return tmp_path


@pytest.fixture
def fake_clonecast(monkeypatch):
    monkeypatch.setattr(episode, "CloneCastCLI", FakeCloneCastCLI)
    monkeypatch.setattr(cc, "CloneCastCLI", FakeCloneCastCLI)


_RESEARCH_MESSAGE = "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."


def test_path_traversal_upload_is_rejected(isolated_data_dir, fake_clonecast, tmp_path):
    repo = make_repo(tmp_path)
    client = TestClient(fastapi_app)
    session = client.post("/api/sessions", json={"repo_path": str(repo), "message": _RESEARCH_MESSAGE}).json()
    assert session["pending_question"]["field"] == "research"
    resp = client.post(
        f"/api/sessions/{session['session_id']}/upload?kind=research",
        files={"file": ("../../../etc/passwd", b"not really passwd", "text/plain")},
    )
    # The traversal is neutralized (basename-only), not silently accepted as
    # a path outside the managed area, and the wrong extension is rejected.
    assert resp.status_code == 400


def test_unsupported_executable_upload_is_rejected(isolated_data_dir, fake_clonecast, tmp_path):
    repo = make_repo(tmp_path)
    client = TestClient(fastapi_app)
    session = client.post("/api/sessions", json={"repo_path": str(repo), "message": _RESEARCH_MESSAGE}).json()
    assert session["pending_question"]["field"] == "research"
    resp = client.post(
        f"/api/sessions/{session['session_id']}/upload?kind=research",
        files={"file": ("payload.sh", b"#!/bin/sh\nrm -rf /", "application/x-sh")},
    )
    assert resp.status_code == 400
    assert "unsupported file type" in resp.json()["detail"]


def test_invalid_session_id_in_url_is_rejected(isolated_data_dir):
    client = TestClient(fastapi_app)
    resp = client.get("/api/sessions/../../etc/passwd")
    assert resp.status_code in (404, 400)


def test_audio_route_cannot_serve_arbitrary_local_path(isolated_data_dir, fake_clonecast, tmp_path):
    repo = make_repo(tmp_path)
    client = TestClient(fastapi_app)
    session = client.post("/api/sessions", json={"repo_path": str(repo), "message": "start"}).json()
    # No episode/audio exists yet for this brand new session - must 404, and
    # there is no query parameter anywhere that accepts a filesystem path.
    resp = client.get(f"/api/sessions/{session['session_id']}/audio")
    assert resp.status_code == 404
    resp = client.get(f"/api/sessions/{session['session_id']}/audio", params={"path": "/etc/passwd"})
    assert resp.status_code == 404


def test_duplicate_answer_submission_does_not_duplicate_backend_work(isolated_data_dir, fake_clonecast, tmp_path):
    repo = make_repo(tmp_path)
    client = TestClient(fastapi_app)
    session = client.post(
        "/api/sessions",
        json={"repo_path": str(repo), "message": "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."},
    ).json()
    session_id = session["session_id"]
    files = {"file": ("research.txt", b"research body", "text/plain")}
    upload = client.post(f"/api/sessions/{session_id}/upload?kind=research", files=files).json()

    first = client.post(f"/api/sessions/{session_id}/answer", json={"upload_id": upload["upload_id"]})
    assert first.status_code == 200
    second = client.post(f"/api/sessions/{session_id}/answer", json={"upload_id": upload["upload_id"]})
    assert second.status_code == 409

    ep_session_id = first.json()["episode_session_id"]
    ep = episode.load_session(ep_session_id)
    ingest_calls = [c for c in ep.clonecast_commands if c["command"][0] == "research-ingest"]
    assert len(ingest_calls) == 1


def test_no_publish_route_exists():
    for route in fastapi_app.routes:
        path = getattr(route, "path", "")
        assert "publish" not in path.lower()


def test_approving_audio_never_calls_a_publishing_command(isolated_data_dir, fake_clonecast, tmp_path):
    repo = make_repo(tmp_path)
    client = TestClient(fastapi_app)
    session = client.post(
        "/api/sessions",
        json={"repo_path": str(repo), "message": "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."},
    ).json()
    session_id = session["session_id"]
    r1 = client.post(f"/api/sessions/{session_id}/upload?kind=research", files={"file": ("r.txt", b"research", "text/plain")}).json()
    d = client.post(f"/api/sessions/{session_id}/answer", json={"upload_id": r1["upload_id"]}).json()
    r2 = client.post(f"/api/sessions/{session_id}/upload?kind=script", files={"file": ("s.txt", b"Host: hi\n", "text/plain")}).json()
    d = client.post(f"/api/sessions/{session_id}/answer", json={"upload_id": r2["upload_id"]}).json()
    d = client.post(f"/api/sessions/{session_id}/answer", json={"text": "Elias Voss"}).json()
    voice_id = d["pending_question"]["options"][0]["value"]
    d = client.post(f"/api/sessions/{session_id}/answer", json={"value": voice_id}).json()
    d = client.post(f"/api/sessions/{session_id}/answer", json={"value": "yes"}).json()
    assert d["pending_question"]["field"] == "listening_gate_action"
    d = client.post(f"/api/sessions/{session_id}/answer", json={"value": "approve"}).json()

    ep = episode.load_session(d["episode_session_id"])
    assert not any("publi" in str(cmd).lower() for cmd in ep.clonecast_commands)
    assert d["workflow_summary"]["publishing_lock_status"] == "eligible"


def test_app_js_never_assigns_untrusted_text_via_innerHTML():
    content = Path(config.BASE_DIR, "app", "static", "app.js").read_text(encoding="utf-8")
    literal = re.compile(r'^"[^"$]*"$|^\'[^\'$]*\'$')
    for match in re.finditer(r"\.innerHTML\s*=\s*(.+);", content):
        rhs = match.group(1).strip()
        assert literal.match(rhs), f"unsafe innerHTML assignment: {match.group(0)}"


def test_session_message_text_is_stored_verbatim_not_executed(isolated_data_dir):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path="/tmp/x")
    payload = "<script>alert(1)</script>"
    app.add_message(store.ChatMessage(role="user", kind="text", text=payload))
    store.save_session(app)
    loaded = store.load_session(app.session_id)
    assert loaded.messages[0].text == payload

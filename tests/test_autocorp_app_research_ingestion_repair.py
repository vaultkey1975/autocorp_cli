"""Regression tests for the pasted-research-text ingestion bug.

Root cause: pasted research/script text was handed to the guided operator's
prompt loop as a raw string. brains/guided_clonecast_episode.read_source
treats a non-"@" answer as a candidate filesystem path first
(``Path(value).exists()``); a long paragraph with no path separators becomes
one oversized path *component*, and that stat() call can raise
``OSError: [Errno 36] File name too long`` instead of cleanly falling
through to "this is not a path, treat it as pasted text". The fix (in
app/chat_controller.py and app/file_service.py) always writes pasted text to
a short, safely-named managed file first and hands the operator a real path,
so the "is this a path" branch is never taken with untrusted pasted content.
"""

from pathlib import Path

import pytest

import config
from app import chat_controller as controller
from app import file_service
from app import session_store as store
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import FakeCloneCastCLI, make_repo


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    return data


def _factory(path):
    return FakeCloneCastCLI(path)


def _long_research_text() -> str:
    # Comfortably over Linux's 255-byte NAME_MAX with no path separators -
    # exactly the shape that used to crash Path(value).exists().
    return "Bigfoot sightings across the Pacific Northwest. " * 40


def test_long_pasted_research_does_not_crash_and_is_saved_as_a_file(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(
        str(repo), "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.", clonecast_cli_factory=_factory
    )
    assert app.pending_question["field"] == "research"

    long_text = _long_research_text()
    assert len(long_text.encode("utf-8")) > 255  # would previously blow past NAME_MAX

    app = controller.submit_answer(app.session_id, {"text": long_text})

    # No crash: the session must have advanced past research entirely.
    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"
    assert app.status != "failed"


def test_pasted_research_is_never_passed_to_path_exists(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(
        str(repo), "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.", clonecast_cli_factory=_factory
    )
    long_text = _long_research_text()
    app = controller.submit_answer(app.session_id, {"text": long_text})

    ep = episode.load_session(app.episode_session_id)
    # research_source must record a *file*, not the raw pasted text treated
    # as a (non-existent, oversized) path.
    assert ep.research_source.get("kind") == "file"
    saved_path = Path(ep.research_source["path"])
    assert saved_path.is_file()
    assert saved_path.read_text(encoding="utf-8") == long_text


def test_safe_generated_filename_for_pasted_research(isolated_data_dir, tmp_path):
    session_id = "appsess_testfixed123"
    managed = file_service.save_pasted_text(session_id, "research", "short research body")
    name = Path(managed).name
    assert name == f"research_{session_id}.txt"
    assert len(name) < 100


def test_safe_generated_filename_for_pasted_script(isolated_data_dir, tmp_path):
    session_id = "appsess_testfixed456"
    managed = file_service.save_pasted_text(session_id, "script", "Host: hello.\n")
    assert Path(managed).name == f"script_{session_id}.txt"


def test_utf8_content_is_preserved_exactly(isolated_data_dir, tmp_path):
    session_id = "appsess_utf8"
    text = "Bigfoot — café, naïve, 日本語, emoji 🦶🌲, and a long tail. " * 10
    managed = file_service.save_pasted_text(session_id, "research", text)
    assert Path(managed).read_text(encoding="utf-8") == text
    assert Path(managed).read_bytes() == text.encode("utf-8")


def test_empty_pasted_text_is_rejected_cleanly(isolated_data_dir):
    with pytest.raises(file_service.FileServiceError, match="empty"):
        file_service.save_pasted_text("appsess_empty", "research", "   ")


def test_pdf_upload_shows_clear_unsupported_message_not_a_utf8_crash(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(
        str(repo), "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.", clonecast_cli_factory=_factory
    )
    # Real, non-UTF-8-decodable PDF-shaped bytes.
    pdf_bytes = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog >>\nendobj\n\xff\xfe\x00\x01binarystream"
    rec = controller.register_upload(app.session_id, "research", "bigfoot.pdf", pdf_bytes)
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})

    assert app.status == "failed"
    assert "PDF text extraction is not yet supported" in app.error["safe_message"]
    assert "UnicodeDecodeError" not in app.error["safe_message"]


def test_resume_after_research_repair_preserves_show_and_duration(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(
        str(repo), "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.", clonecast_cli_factory=_factory
    )
    pdf_bytes = b"%PDF-1.4\n\xff\xfe not utf-8 \x80\x81"
    rec = controller.register_upload(app.session_id, "research", "bad.pdf", pdf_bytes)
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    assert app.status == "failed"

    ep_before = episode.load_session(app.episode_session_id)
    assert ep_before.selected_studio_show
    assert ep_before.requested_duration_seconds == 600

    app = controller.resume_session(app.session_id, clonecast_cli_factory=_factory)
    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "research"

    long_text = _long_research_text()
    app = controller.submit_answer(app.session_id, {"text": long_text})
    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"

    ep_after = episode.load_session(app.episode_session_id)
    assert ep_after.selected_studio_show == ep_before.selected_studio_show
    assert ep_after.requested_duration_seconds == 600

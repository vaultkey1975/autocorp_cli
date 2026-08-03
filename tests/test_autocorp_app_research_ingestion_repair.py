"""Regression tests for explicit research input provenance."""

from __future__ import annotations

import builtins
import errno
import json
from pathlib import Path

import pytest

import config
from app import chat_controller as controller
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


def _start_research_session(repo: Path) -> store.AppSession:
    app = controller.start_session(
        str(repo),
        "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.",
        clonecast_cli_factory=_factory,
    )
    assert app.pending_question["field"] == "research"
    return app


def _long_markdown_research() -> str:
    return "\n".join(
        [
            "# Paranormal Bigfoot-Type Phenomena Around the World",
            "",
            "## Executive summary",
            "Bigfoot-type claims include Sasquatch, Yowie, Yeti, Almasty, and Orang Pendek.",
            "",
            "| Region | Claim | Evidence note |",
            "|---|---|---|",
            "| Pacific Northwest | Sasquatch | footprints, reports, disputed film |",
            "| Himalaya | Yeti | bear DNA in tested samples |",
            "",
            (
                "Citations: [1], (Daegling 2004), Sykes et al. 2014; unusual chars: "
                "“quotes”, é, 日本語, 🦶, \\ue200cite\\ue202turn1\\ue201."
            ),
            "",
            *[f"Paragraph {i}: " + ("a" * 180) for i in range(80)],
        ]
    )


def _managed_research_body(ep: episode.EpisodeSession) -> str:
    path = Path(ep.artifact_paths["managed_research"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["body"]


def test_short_pasted_research_message_works(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)

    app = controller.submit_answer(app.session_id, {"text": "Short research body."})

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"
    ep = episode.load_session(app.episode_session_id)
    assert ep.research_source["source_type"] == "pasted_text"
    assert ep.research_source["text"] == "Short research body."
    assert _managed_research_body(ep) == "Short research body."


def test_very_long_multiline_pasted_markdown_is_preserved_with_checksum(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    text = _long_markdown_research()

    app = controller.submit_answer(app.session_id, {"text": text})

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"
    ep = episode.load_session(app.episode_session_id)
    checksum = episode.checksum_bytes(text.encode("utf-8"))
    assert ep.research_source["source_type"] == "pasted_text"
    assert ep.research_source["text"] == text
    assert ep.research_source["sha256"] == checksum
    assert ep.validation_evidence["research_source"]["sha256"] == checksum
    assert _managed_research_body(ep) == text


def test_pasted_markdown_tables_citations_and_unicode_are_preserved(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    text = "# Heading\n\n| A | B |\n|---|---|\n| “α” | citation [12]; \\ue200cite\\ue202turn42\\ue201 |\n"

    app = controller.submit_answer(app.session_id, {"text": text})

    ep = episode.load_session(app.episode_session_id)
    assert ep.research_source["text"] == text
    assert _managed_research_body(ep) == text


def test_no_filesystem_function_receives_pasted_research_body(isolated_data_dir, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    text = _long_markdown_research()

    original_exists = Path.exists
    original_is_file = Path.is_file
    original_stat = Path.stat
    original_resolve = Path.resolve
    original_open = builtins.open

    def _guard_path(path_obj):
        if str(path_obj) == text:
            raise AssertionError("pasted research body was used as a filesystem path")

    def guarded_exists(self):
        _guard_path(self)
        return original_exists(self)

    def guarded_is_file(self):
        _guard_path(self)
        return original_is_file(self)

    def guarded_stat(self, *args, **kwargs):
        _guard_path(self)
        return original_stat(self, *args, **kwargs)

    def guarded_resolve(self, *args, **kwargs):
        _guard_path(self)
        return original_resolve(self, *args, **kwargs)

    def guarded_open(file, *args, **kwargs):
        if str(file) == text:
            raise AssertionError("pasted research body was opened as a file")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", guarded_exists)
    monkeypatch.setattr(Path, "is_file", guarded_is_file)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(builtins, "open", guarded_open)

    app = controller.submit_answer(app.session_id, {"text": text})

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"


def test_real_uploaded_txt_path_still_works(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    rec = controller.register_upload(app.session_id, "research", "research.txt", b"Uploaded TXT research.")

    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})

    ep = episode.load_session(app.episode_session_id)
    assert ep.research_source["source_type"] == "uploaded_file"
    assert ep.research_source["path"] == str(Path(rec.managed_path).resolve())
    assert ep.research_source["sha256"] == rec.managed_sha256
    assert Path(ep.artifact_paths["managed_research"]) == Path(rec.managed_path).resolve()


def test_real_uploaded_pdf_path_follows_upload_workflow(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    fake = FakeCloneCastCLI(repo)
    app = controller.start_session(
        str(repo),
        "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.",
        clonecast_cli_factory=lambda _: fake,
    )
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
    rec = controller.register_upload(app.session_id, "research", "bigfoot.pdf", pdf_bytes)

    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"
    ep = episode.load_session(app.episode_session_id)
    assert ep.research_source["source_type"] == "uploaded_file"
    assert ep.research_source["path"].endswith(".pdf")
    ingest = next(call for call in fake.calls if call[0] == "research-ingest")
    assert ingest[1] == ep.research_source["path"]


def test_saved_failed_session_resumes_using_original_research_without_duplication(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = store.AppSession(
        session_id=store.new_session_id(),
        clonecast_repo_path=str(repo),
        status="failed",
    )
    ep = episode.EpisodeSession(session_id="acce_failed_research", clonecast_repo_path=str(repo.resolve()))
    ep.selected_studio_show = "studio_c7599bb4733e438d9f1926e0e4ad6111"
    ep.requested_duration_seconds = 600
    episode.save_session(ep)
    app.episode_session_id = ep.session_id
    recovered = _long_markdown_research()
    app.add_message(
        store.ChatMessage(role="assistant", kind="question", text="Add your research", input_kind="file_or_text")
    )
    app.add_message(store.ChatMessage(role="user", kind="text", text="(pasted text provided)"))
    app.add_message(
        store.ChatMessage(
            role="assistant",
            kind="error",
            text=(
                "AutoCorp hit an unexpected internal error. Your session was saved. "
                "Press Resume after the problem is fixed."
            ),
            technical_detail=f"OSError: [Errno 36] File name too long: '{recovered}'",
        )
    )
    store.save_session(app)
    fake = FakeCloneCastCLI(repo)

    resumed = controller.resume_session(app.session_id, clonecast_cli_factory=lambda _: fake)

    assert resumed.status == "awaiting_input"
    assert resumed.pending_question["field"] == "script"
    saved = episode.load_session(ep.session_id)
    assert saved.research_source["source_type"] == "pasted_text"
    assert saved.research_source["text"] == recovered
    assert _managed_research_body(saved) == recovered
    assert saved.research_source["text"].count("# Paranormal Bigfoot-Type Phenomena") == 1
    assert len([call for call in fake.calls if call[0] == "research-ingest"]) == 1


def test_stale_historical_research_prompt_resumes_using_original_research(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = store.AppSession(
        session_id=store.new_session_id(),
        clonecast_repo_path=str(repo),
        status="awaiting_input",
    )
    ep = episode.EpisodeSession(session_id="acce_stale_research_prompt", clonecast_repo_path=str(repo.resolve()))
    ep.selected_studio_show = "studio_c7599bb4733e438d9f1926e0e4ad6111"
    ep.requested_duration_seconds = 900
    episode.save_session(ep)
    app.episode_session_id = ep.session_id
    app.pending_question = {
        "field": "research",
        "text": "Add your research (PDF or TXT) or paste research text. Research is required before generation.",
        "input_kind": "file_or_text",
    }
    recovered = _long_markdown_research()
    app.add_message(
        store.ChatMessage(
            role="assistant", kind="question", text=app.pending_question["text"], input_kind="file_or_text"
        )
    )
    app.add_message(store.ChatMessage(role="user", kind="text", text="(pasted text provided)"))
    app.add_message(
        store.ChatMessage(
            role="assistant",
            kind="error",
            text=(
                "AutoCorp hit an unexpected internal error. Your session was saved. "
                "Your session was saved. Press Resume after the problem is fixed."
            ),
            technical_detail=f"OSError: [Errno 36] File name too long: '{recovered}'",
        )
    )
    app.add_message(store.ChatMessage(role="system", kind="text", text="Resuming from the last completed step."))
    app.add_message(store.ChatMessage(role="assistant", kind="progress", text="CloneCast target confirmed."))
    app.add_message(
        store.ChatMessage(
            role="assistant", kind="question", text=app.pending_question["text"], input_kind="file_or_text"
        )
    )
    store.save_session(app)
    fake = FakeCloneCastCLI(repo)

    resumed = controller.resume_session(app.session_id, clonecast_cli_factory=lambda _: fake)

    assert resumed.status == "awaiting_input"
    assert resumed.pending_question["field"] == "script"
    saved = episode.load_session(ep.session_id)
    assert saved.research_source["source_type"] == "pasted_text"
    assert saved.research_source["text"] == recovered
    assert _managed_research_body(saved) == recovered
    assert saved.research_source["sha256"] == episode.checksum_bytes(recovered.encode("utf-8"))
    assert len([call for call in fake.calls if call[0] == "research-ingest"]) == 1
    assert resumed.messages[-1].kind == "question"
    assert resumed.messages[-1].text.startswith("Now add the final approved script")

    resumed_again = controller.resume_session(app.session_id, clonecast_cli_factory=lambda _: fake)
    saved_again = episode.load_session(ep.session_id)

    assert resumed_again.pending_question["field"] == "script"
    assert saved_again.completed_stage == "research_accepted"
    assert len([call for call in fake.calls if call[0] == "research-ingest"]) == 1


def test_research_checksum_remains_stable(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    text = _long_markdown_research()
    expected = episode.checksum_bytes(text.encode("utf-8"))

    app = controller.submit_answer(app.session_id, {"text": text})

    ep = episode.load_session(app.episode_session_id)
    assert ep.research_source["sha256"] == expected
    assert episode.checksum_bytes(ep.research_source["text"].encode("utf-8")) == expected
    assert episode.checksum_bytes(_managed_research_body(ep).encode("utf-8")) == expected


def test_error_message_is_not_duplicated(isolated_data_dir, tmp_path):
    app = store.AppSession(session_id=store.new_session_id(), clonecast_repo_path=str(tmp_path), status="running")
    store.save_session(app)

    controller._on_worker_error(
        app.session_id,
        "AutoCorp hit an unexpected internal error.",
        technical="boom",
        retry_safe=True,
    )

    saved = store.load_session(app.session_id)
    assert saved.messages[-1].text.count("Your session was saved.") == 1


def test_errno_36_cannot_occur_from_pasted_research(isolated_data_dir, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    app = _start_research_session(repo)
    text = "x" * 5000

    def explode_if_body_path(self):
        if str(self) == text:
            raise OSError(errno.ENAMETOOLONG, "File name too long", text)
        return False

    monkeypatch.setattr(Path, "exists", explode_if_body_path)

    app = controller.submit_answer(app.session_id, {"text": text})

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "script"


def test_guided_operator_read_source_treats_enametoolong_as_pasted_text(monkeypatch):
    text = "# Long research\n" + ("x" * 5000)

    def explode_if_body_path(self):
        if str(self) == text:
            raise OSError(errno.ENAMETOOLONG, "File name too long", text)
        return False

    monkeypatch.setattr(Path, "exists", explode_if_body_path)

    source, data = episode.read_source(text, label="research")

    assert source["source_type"] == "pasted_text"
    assert source["text"] == text
    assert data == text.encode("utf-8")

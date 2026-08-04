"""Tests for the real, pre-generation speech-only review AutoCorp shows
before every Start Generation, and for the guarantees around it: AutoCorp
never re-implements CloneCast's transform, never writes to CloneCast's
database directly, keeps publishing locked, and works for any show/host/
voice without hardcoding.
"""

import inspect

import pytest

import config
from app import chat_controller as controller
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import (
    STUDIO_DISPLAY_NAME,
    STUDIO_ID,
    VOICE_LARRY_APPROVED,
    FakeCloneCastCLI,
    make_repo,
)

READY_PROMPT = "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."
SCRIPT_WITH_STRUCTURE = (
    b"OPEN\n\nChapter 1: Into the Forest\n\nHost: Real dialogue about the case.\n"
)


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    return data


def _factory(path):
    return FakeCloneCastCLI(path)


def _drive_to_preview(tmp_path, script_bytes=b"Host: Approved script body.\n", factory=_factory):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", script_bytes)
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})
    return app


def test_speech_preview_message_shown_before_start_generation_answered(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    assert app.pending_question["field"] == "start_generation"
    preview_messages = [m for m in app.messages if m.kind == "speech_preview"]
    assert len(preview_messages) == 1
    msg = preview_messages[0]
    assert "Transformation version" in msg.text
    assert "Approved-script checksum" in msg.text
    assert "Rendered speech-text checksum" in msg.text


def test_original_script_and_derived_speech_text_both_shown_unaltered(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    msg = next(m for m in app.messages if m.kind == "speech_preview")
    assert "ORIGINAL APPROVED SCRIPT" in msg.technical_detail
    assert SCRIPT_WITH_STRUCTURE.decode("utf-8") in msg.technical_detail
    assert "DERIVED SPEECH-ONLY TEXT" in msg.technical_detail
    assert "OPEN" not in msg.technical_detail.split("DERIVED SPEECH-ONLY TEXT")[1].split("REMOVED STRUCTURAL")[0]
    assert "Into the Forest" in msg.technical_detail
    assert "Real dialogue about the case." in msg.technical_detail


def test_removed_headings_and_cues_reflected_in_preview(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    ep = episode.load_session(app.episode_session_id)
    preview = ep.validation_evidence["speech_text_preview"]
    assert "OPEN" in preview["removed_headings"]
    assert "Into the Forest" in preview["retained_titles"]
    msg = next(m for m in app.messages if m.kind == "speech_preview")
    assert "Structural headings removed: 1" in msg.text


def test_approved_script_preserved_byte_for_byte_after_preview(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    ep = episode.load_session(app.episode_session_id)
    from pathlib import Path

    stored = Path(str(ep.artifact_paths["managed_script"])).read_bytes()
    assert stored == SCRIPT_WITH_STRUCTURE


def test_autocorp_does_not_implement_an_independent_speech_filter():
    """AutoCorp must call CloneCast's canonical transformation, never
    reimplement heading/cue stripping itself. Grep the real source (not the
    test-only fake double) for the tell-tale patterns a competing
    implementation would need."""
    # Precise implementation-only signals (function/class/regex names a
    # competing transform would need) - not display-label words like "SFX"
    # or "MUSIC", which legitimately appear in the review message's own
    # human-readable summary text.
    source = inspect.getsource(episode)
    controller_source = inspect.getsource(controller)
    forbidden_fragments = ["derive_speech_text", "_HEADING_RE", "ProductionCue", "SpeechTextResult"]
    for fragment in forbidden_fragments:
        assert fragment not in source, f"AutoCorp appears to reimplement transform logic: {fragment!r}"
        assert fragment not in controller_source, f"AutoCorp appears to reimplement transform logic: {fragment!r}"


def test_no_direct_clonecast_database_access():
    source = inspect.getsource(episode)
    controller_source = inspect.getsource(controller)
    for forbidden in ("sqlite3", "cloneshow.db", "import sqlite"):
        assert forbidden not in source
        assert forbidden not in controller_source


def test_start_generation_uses_the_reviewed_checksum(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    ep = episode.load_session(app.episode_session_id)
    reviewed_checksum = ep.validation_evidence["speech_text_preview"]["approved_script_checksum"]
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"
    ep = episode.load_session(app.episode_session_id)
    assert reviewed_checksum == ep.script_checksum


def test_repeated_start_generation_creates_only_one_job(isolated_data_dir, tmp_path):
    instances = []

    def factory(path):
        fake = FakeCloneCastCLI(path)
        instances.append(fake)
        return fake

    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE, factory=factory)
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"
    render_calls = [c for c in instances[-1].calls if c and c[0] == "speech-render"]
    assert len(render_calls) == 1
    idempotency_keys = {c[c.index("--idempotency-key") + 1] for c in render_calls}
    assert len(idempotency_keys) == 1


def test_publishing_stays_locked_through_preview_and_generation(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    ep = episode.load_session(app.episode_session_id)
    assert ep.owner_approval_status == "publishing_locked"
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    ep = episode.load_session(app.episode_session_id)
    assert ep.owner_approval_status == "publishing_locked"
    assert app.pending_question["field"] == "listening_gate_action"


def test_clonecast_error_during_preview_remains_visible(isolated_data_dir, tmp_path):
    class FailingPreview(FakeCloneCastCLI):
        def checked(self, args, *, input_text=None):
            if args and args[0] == "speech-text-preview":
                self.calls.append(args)
                raise episode.EpisodeBuildError("CloneCast command failed: speech-text-preview")
            return super().checked(args, input_text=input_text)

    def factory(path):
        return FailingPreview(path)

    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", SCRIPT_WITH_STRUCTURE)
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})

    assert app.status == "failed"
    assert app.error is not None
    assert "speech-text-preview" in app.error["technical_message"]
    assert any(m.kind == "error" for m in app.messages)


def test_stage_order_places_preview_and_save_for_later_between_import_and_voice():
    order = episode._STAGE_ORDER
    assert order["approved_script_imported"] < order["speech_text_previewed"] < order["voice_assigned"]
    assert order["speech_text_previewed"] < order["saved_before_generation"] < order["voice_assigned"]


def test_regenerate_section_invalidates_real_clonecast_segment(isolated_data_dir, tmp_path):
    app = _drive_to_preview(tmp_path, SCRIPT_WITH_STRUCTURE)
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"
    ep = episode.load_session(app.episode_session_id)
    speech_job_id = ep.clonecast_episode_identifiers["speech_job_id"]
    segment_id = ep.validation_evidence["segment_ids"][0]
    ep.validation_evidence["failed_sections"] = [segment_id]
    episode.save_session(ep)

    fake = FakeCloneCastCLI(episode.Path(ep.clonecast_repo_path))
    result = episode.regenerate_section(ep.session_id, segment_id, clonecast_factory=lambda _p: fake)
    assert (speech_job_id, segment_id) in fake.invalidated_segments
    assert result.completed_stage == "section_regeneration_requested"


def test_second_show_and_different_voice_work_without_hardcoding(isolated_data_dir, tmp_path):
    """Nothing in AutoCorp's guided flow may special-case the studio/voice
    fixture used elsewhere in this suite - a completely different show name
    and voice profile id must work identically."""

    class SecondShowCloneCastCLI(FakeCloneCastCLI):
        def checked(self, args, *, input_text=None):
            if args == ["radio-studio-list"]:
                self.calls.append(args)
                data = [{"studio_id": "studio_nightwatch", "display_name": "Nightwatch Radio", "lifecycle_status": "approved"}]
                return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
            if args and args[0] == "voice-list":
                self.calls.append(args)
                data = [{"voice_profile_id": "voice_dr_amara", "display_name": "Dr. Amara", "lifecycle_status": "approved", "stable_name": "amara.v1", "version_label": "amara-v1", "version_number": 1}]
                return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
            return super().checked(args, input_text=input_text)

    def factory(path):
        return SecondShowCloneCastCLI(path)

    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), "Create a Nightwatch Radio episode. 15 minutes. No guests. Audio only.", clonecast_cli_factory=factory)
    assert app.pending_question["field"] == "research"
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: A completely different show's dialogue.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Dr. Amara"})
    assert app.pending_question["field"] == "voice"
    assert "voice_dr_amara" in [o["value"] for o in app.pending_question["options"]]
    app = controller.submit_answer(app.session_id, {"value": "voice_dr_amara"})
    assert app.pending_question["field"] == "start_generation"
    preview = next(m for m in app.messages if m.kind == "speech_preview")
    assert "different show's dialogue" in preview.technical_detail
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"
    ep = episode.load_session(app.episode_session_id)
    assert ep.selected_studio_show == "Nightwatch Radio"
    assert ep.selected_voices["host"] == "voice_dr_amara"


if __name__ == "__main__":
    pytest.main([__file__])

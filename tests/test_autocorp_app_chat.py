import pytest

import config
from app import chat_controller as controller
from app import session_store as store
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import (
    STUDIO_DISPLAY_NAME,
    STUDIO_ID,
    VOICE_DANIEL_APPROVED,
    VOICE_LARRY_APPROVED,
    VOICE_LARRY_DRAFT,
    FakeCloneCastCLI,
    make_repo,
)

READY_PROMPT = "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only."


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    # These tests exercise the guided workflow end-to-end with a fake
    # CloneCast CLI; the real-GPU coordination guard has its own dedicated
    # tests in test_autocorp_app_gpu_guard.py and is disabled here so this
    # file stays fast and independent of the machine's actual GPU state.
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    return data


def _factory(_path):
    return FakeCloneCastCLI(_path)


def test_one_question_at_a_time_and_known_choices_render_as_buttons(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=_factory)
    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "research"
    assert app.pending_question["input_kind"] == "file_or_text"
    # studio/duration/guests/media were all extracted from the first message
    # and must never be asked again.
    fields_asked = [m.text for m in app.messages if m.kind == "question"]
    assert len(fields_asked) == 1


def test_studio_question_renders_friendly_buttons_when_not_prefilled(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), "start an episode", clonecast_cli_factory=_factory)
    assert app.pending_question["field"] == "studio"
    labels = [o["label"] for o in app.pending_question["options"]]
    assert STUDIO_DISPLAY_NAME in labels
    values = [o["value"] for o in app.pending_question["options"]]
    assert STUDIO_ID in values


def test_full_guided_flow_reaches_listening_gate_with_approved_voice(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=_factory)
    assert app.pending_question["field"] == "research"

    rec = controller.register_upload(app.session_id, "research", "bigfoot.txt", b"Bigfoot research body.")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    events = [m.event for m in app.messages if m.kind == "progress"]
    assert "research_imported" in events
    assert "research_validated" in events
    assert "research_accepted" in events

    assert app.pending_question["field"] == "script"
    rec = controller.register_upload(app.session_id, "script", "script.txt", b"Host: Approved script body.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})

    assert app.pending_question["field"] == "host"
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})

    assert app.pending_question["field"] == "voice"
    voice_ids = [o["value"] for o in app.pending_question["options"]]
    assert VOICE_LARRY_APPROVED in voice_ids
    assert VOICE_LARRY_DRAFT not in voice_ids  # drafts hidden by default
    assert VOICE_DANIEL_APPROVED in voice_ids
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})

    assert app.pending_question["field"] == "start_generation"
    app = controller.submit_answer(app.session_id, {"value": "yes"})

    # Script import and voice assignment are CloneCast calls that only
    # actually happen once generation starts - so their progress events only
    # appear now, not right after the earlier UI answers were given.
    events = [m.event for m in app.messages if m.kind == "progress"]
    assert "approved_script_imported" in events
    assert "voice_assignment_verified" in events
    assert "audio_generation_started" in events

    assert app.status == "awaiting_input"
    assert app.pending_question["field"] == "listening_gate_action"
    assert any(m.kind == "listening_gate" for m in app.messages)

    ep = episode.load_session(app.episode_session_id)
    assert ep.selected_host == "Elias Voss"
    assert ep.selected_voices["host"] == VOICE_LARRY_APPROVED
    assert ep.guests_or_callers == "no"
    assert ep.media_mode == "audio-only"
    assert ep.owner_approval_status == "publishing_locked"


def test_resume_after_speech_failure_auto_continues_past_start_gate(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    failures_remaining = {"count": 1}
    instances = []

    class OneSpeechFailureThenSuccess(FakeCloneCastCLI):
        def checked_monitored(self, args, *, timeout, heartbeat_interval, heartbeat):
            self.calls.append(args)
            if args and args[0] == "speech-render" and failures_remaining["count"]:
                failures_remaining["count"] -= 1
                heartbeat(heartbeat_interval)
                raise episode.EpisodeBuildError("Chatterbox lifecycle worker response timed out")
            return self.checked(args)

    def factory(path):
        fake = OneSpeechFailureThenSuccess(path)
        instances.append(fake)
        return fake

    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: Approved script body.\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})
    app = controller.submit_answer(app.session_id, {"value": "yes"})

    assert app.status == "failed"
    failed_ep = episode.load_session(app.episode_session_id)
    assert failed_ep.failed_stage == "speech_render"
    first_start_questions = [m for m in app.messages if m.kind == "question" and m.text.startswith("Everything is set")]
    assert len(first_start_questions) == 1

    controller._unregister(app.session_id)
    resumed = controller.resume_session(app.session_id, clonecast_cli_factory=factory)

    assert resumed.status == "awaiting_input"
    assert resumed.pending_question["field"] == "listening_gate_action"
    start_questions = [m for m in resumed.messages if m.kind == "question" and m.text.startswith("Everything is set")]
    assert len(start_questions) == 1
    completed_ep = episode.load_session(resumed.episode_session_id)
    assert completed_ep.completed_stage == "listening_gate"
    assert completed_ep.failed_stage is None
    assert (
        completed_ep.clonecast_episode_identifiers["research_id"]
        == failed_ep.clonecast_episode_identifiers["research_id"]
    )
    assert (
        completed_ep.clonecast_episode_identifiers["script_id"]
        == failed_ep.clonecast_episode_identifiers["script_id"]
    )
    assert completed_ep.script_checksum == failed_ep.script_checksum
    assert completed_ep.script_preserved_byte_for_byte is True
    resume_calls = instances[-1].calls
    assert not any(call[0] == "episode-create" for call in resume_calls)
    assert not any(call[0] == "episode-script-import-approved" for call in resume_calls)
    assert any(call[0] == "speech-render" for call in resume_calls)


def test_draft_voice_requires_explicit_override(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=_factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: hi\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})

    voice_ids = [o["value"] for o in app.pending_question["options"]]
    assert VOICE_LARRY_DRAFT not in voice_ids

    with pytest.raises(controller.ChatControllerError):
        controller.submit_answer(app.session_id, {"value": VOICE_LARRY_DRAFT})

    controller.unlock_voice_advanced(app.session_id)
    app = store.load_session(app.session_id)
    # Re-issue the voice question so it reflects the unlocked catalog. The
    # pending question is still open; drafts must now be selectable only
    # through the explicit two-step confirmation, never silently.
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_DRAFT})
    assert app.pending_question["field"] == "voice"
    assert "needs confirmation" in app.messages[-1].text or "confirmation" in app.pending_question["text"]
    assert next(o for o in app.pending_question["options"] if o["value"] == VOICE_LARRY_DRAFT)
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_DRAFT, "confirm_draft": True})
    assert app.pending_question["field"] == "start_generation"
    ep = episode.load_session(app.episode_session_id)
    assert ep.selected_voices["host"] == VOICE_LARRY_DRAFT


def test_host_name_cannot_be_used_as_voice_id(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=_factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: hi\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    # The voice question only ever accepts a known voice_profile_id; typing
    # the host's own name is rejected before it ever reaches CloneCast.
    with pytest.raises(controller.ChatControllerError):
        controller.submit_answer(app.session_id, {"text": "Elias Voss"})


def test_approve_records_listening_approval_and_keeps_publishing_locked(isolated_data_dir, tmp_path):
    repo = make_repo(tmp_path)
    app = controller.start_session(str(repo), READY_PROMPT, clonecast_cli_factory=_factory)
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: hi\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})
    app = controller.submit_answer(app.session_id, {"value": "yes"})
    assert app.pending_question["field"] == "listening_gate_action"

    app = controller.submit_answer(app.session_id, {"value": "approve"})
    assert app.status == "completed"
    ep = episode.load_session(app.episode_session_id)
    assert ep.owner_approval_status == "publishing_eligible"
    assert not any("publication" in str(c).lower() for c in ep.clonecast_commands)
    summary = controller.workflow_summary(app)
    # Even "eligible" only ever means the owner listening gate passed - there
    # is still no publishing route anywhere in the app.
    assert summary["publishing_lock_status"] == "eligible"

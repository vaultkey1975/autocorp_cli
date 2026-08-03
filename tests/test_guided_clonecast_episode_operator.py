import argparse
import json
import subprocess
from pathlib import Path

import pytest

import autocorp
import config
from brains import guided_clonecast_episode as episode


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clonecast"
    (repo / "src" / "clonecast").mkdir(parents=True)
    (repo / "src" / "clonecast" / "cli.py").write_text("# cli\n", encoding="utf-8")
    (repo / "migrations").mkdir()
    return repo


class FakeCloneCast(episode.CloneCastCLI):
    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = []
        self.research_states = {"research_1": "accepted"}
        self.duplicate_of = {}
        self.created_episodes = 0
        self.voice_assignments: dict[str, list[dict[str, str]]] = {}

    def validate_repo(self):
        episode.CloneCastCLI(self.repo).validate_repo()

    def checked(self, args, *, input_text=None):
        self.calls.append(args)
        if args == ["config-check"]:
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {})
        if args == ["radio-studio-list"]:
            data = [{"studio_id": "studio_1", "display_name": "Studio One"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
        if args and args[0] == "research-ingest":
            data = [{"status": "accepted", "research_id": "research_1"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
        if args and args[0] == "research-show":
            research_id = args[1]
            data = {
                "research_id": research_id,
                "lifecycle_state": self.research_states.get(research_id, "accepted"),
                "duplicate_of_research_id": self.duplicate_of.get(research_id),
                "content_hash": "c" * 64,
                "current_path": str(self.repo / "research.txt"),
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "research-recover":
            research_id = args[args.index("--research-id") + 1]
            self.research_states[research_id] = "accepted"
            data = [{"status": "accepted", "research_id": research_id}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
        if args and args[0] == "episode-create":
            self.created_episodes += 1
            data = {"episode_id": "episode_1", "idempotent": False}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "episode-script-import-approved":
            script_file = Path(args[args.index("--script-file") + 1])
            digest = episode.checksum_file(script_file)
            data = {
                "episode_id": "episode_1",
                "script_id": "script_1",
                "source_sha256": digest,
                "stored_sha256": digest,
                "preserved_byte_for_byte": True,
                "status": "approved",
                "voice_ready": True,
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "script-voice-list":
            return episode.CloneCastResult(
                ["python", "-m", "clonecast.cli", *args],
                0,
                "[]",
                "",
                self.voice_assignments.get(args[1], []),
            )
        if args and args[0] == "script-voice-assign":
            script_id = args[args.index("--script-id") + 1]
            speaker = args[args.index("--speaker") + 1]
            voice_profile_id = args[args.index("--voice-profile-id") + 1]
            assignment = {
                "assignment_id": f"assign_{len(self.voice_assignments.get(script_id, [])) + 1}",
                "script_id": script_id,
                "speaker": speaker,
                "voice_profile_id": voice_profile_id,
            }
            self.voice_assignments.setdefault(script_id, []).append(assignment)
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", assignment)
        if args and args[0] == "speech-provider-check":
            data = {"available": True, "provider": "chatterbox-turbo", "preflight": {"may_begin": True, "free_vram_mib": 12000}}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "speech-render":
            data = {"job": {"job_id": "speech_1"}, "segments": [{"segment_render_id": "seg_1", "output_path": str(self.repo / "segment.wav")}]}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "speech-render-validate":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"valid": True})
        if args and args[0] == "episode-audio-assemble":
            final = self.repo / "episode.mp3"
            final.write_bytes(b"x" * 2048)
            data = {
                "job": {"job_id": "audio_1"},
                "outputs": [{"output_type": "mp3", "path": str(final), "duration_seconds": 42.0, "file_size_bytes": final.stat().st_size, "container": "mp3", "codec": "mp3", "sha256": "a" * 64}],
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        if args and args[0] == "episode-audio-validate":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"valid": True})
        if args and args[0] == "episode-audio-master":
            raw = self.repo / "episode.mp3"
            mastered = self.repo / "episode.mastered.mp3"
            if not raw.exists():
                raw.write_bytes(b"x" * 2048)
            mastered.write_bytes(b"m" * 4096)
            data = {
                "job": {
                    "mastering_job_id": "master_1",
                    "status": "completed",
                    "input_i_lufs": -39.1,
                    "input_tp_dbtp": -13.8,
                    "input_lra_lu": 2.0,
                    "output_i_lufs": -16.0,
                    "output_tp_dbtp": -2.1,
                    "output_lra_lu": 2.5,
                },
                "artifacts": [
                    {"artifact_type": "source_mp3", "path": str(raw), "sha256": "a" * 64, "file_size_bytes": raw.stat().st_size, "duration_seconds": 42.0, "container": "mp3", "codec": "mp3"},
                    {"artifact_type": "mastered_mp3", "path": str(mastered), "sha256": "b" * 64, "file_size_bytes": mastered.stat().st_size, "duration_seconds": 42.0, "container": "mp3", "codec": "mp3"},
                ],
                "validation": {"valid": True, "measurements": {"output_i_lufs": -16.0, "output_tp_dbtp": -2.1, "output_lra_lu": 2.5}},
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)
        return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {})

    def discover_commands(self):
        return {
            "radio-studio-list",
            "research-ingest",
            "research-show",
            "research-recover",
            "episode-create",
            "episode-script-import-approved",
            "script-voice-list",
            "script-voice-assign",
            "speech-provider-check",
            "speech-render",
            "speech-render-validate",
            "episode-audio-assemble",
            "episode-audio-validate",
            "episode-audio-master",
        }


class DuplicateResearchCloneCast(FakeCloneCast):
    def __init__(self, repo: Path):
        super().__init__(repo)
        self.research_states = {"research_new": "duplicate", "research_existing": "accepted"}
        self.duplicate_of = {"research_new": "research_existing"}

    def checked(self, args, *, input_text=None):
        if args and args[0] == "research-ingest":
            self.calls.append(args)
            data = [{"status": "duplicate", "research_id": "research_new", "duplicate_of_research_id": "research_existing"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)
        return super().checked(args, input_text=input_text)


class FailingAcceptanceCloneCast(FakeCloneCast):
    def __init__(self, repo: Path):
        super().__init__(repo)
        self.research_states = {"research_processing": "processing"}

    def checked(self, args, *, input_text=None):
        if args and args[0] == "research-recover":
            self.calls.append(args)
            raise episode.EpisodeBuildError("CloneCast command failed: research-recover\nstdout:\n[]\nstderr:\nrecover failed")
        return super().checked(args, input_text=input_text)


def _inputs(values):
    items = iter(values)
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return next(items)

    ask.seen = seen
    return ask


@pytest.fixture
def session_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    return data


def test_episode_build_parser_registers_repo_option():
    parser = autocorp.build_parser()
    args = parser.parse_args(["episode-build", "--repo", "/tmp/clonecast"])
    assert args.repo == "/tmp/clonecast"
    assert args.func is autocorp.cmd_episode_build


def test_duration_parsing():
    assert episode.parse_duration("10m") == 600
    assert episode.parse_duration("90 seconds") == 90
    assert episode.parse_duration("1h") == 3600
    with pytest.raises(ValueError):
        episode.parse_duration("soon")


def test_studio_input_normalizes_id_display_name():
    assert episode.normalize_studio_show("studio_1: Studio One") == "studio_1"
    assert episode.normalize_studio_show("studio_1") == "studio_1"


def test_guided_question_order_and_script_preservation(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Approved script.\nPreserve this exactly.\n", encoding="utf-8")
    ask = _inputs(["YES", "studio_1", "10m", "yes", "research body", "yes", str(script), "Larry", "voice_host", "no", "audio", "yes", "yes", "save"])
    out = []
    completed = episode.run_guided_episode_build(str(repo), input_func=ask, output=out.append, clonecast_factory=FakeCloneCast)
    saved = list((session_data_dir / episode.SESSION_DIRNAME).glob("*.json"))
    assert len(saved) == 1
    session = episode.load_session(saved[0].stem)
    assert session.script_checksum == episode.checksum_file(script)
    assert session.imported_script_checksum == session.script_checksum
    assert session.script_preserved_byte_for_byte is True
    assert session.clonecast_episode_identifiers["episode_id"] == "episode_1"
    assert session.clonecast_episode_identifiers["research_id"] == "research_1"
    assert session.clonecast_episode_identifiers["script_id"] == "script_1"
    assert session.artifact_paths["raw_audio"].endswith("episode.mp3")
    assert session.artifact_paths["final_audio"].endswith("episode.mastered.mp3")
    assert session.validation_evidence["output_i_lufs"] == -16.0
    assert session.validation_evidence["output_tp_dbtp"] == -2.1
    assert session.completed_stage == "listening_gate"
    assert completed.session_id == session.session_id
    assert session.owner_approval_status == "publishing_locked"
    assert session.publishing_lock_reason == episode.PUBLISHING_LOCKED_REASON
    assert "EPISODE READY FOR OWNER REVIEW" in out
    assert [prompt.split("?")[0] for prompt in ask.seen[:6]] == [
        f"Confirm CloneCast target {repo.resolve()} by typing YES: ",
        "Which CloneCast show/studio should be used",
        "How long should the podcast be",
        "Do you have research",
        "Provide research by file path, @file, or pasted text: ",
        "Do you have a final approved script",
    ]
    assert "Research imported" in out
    assert "Research validated" in out
    assert "Research accepted" in out
    assert "Approved script imported" in out
    assert "Voice assigned" in out
    assert "Audio generation started" in out


def test_newly_imported_research_is_accepted_before_episode_create(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    fake = FakeCloneCast(repo)
    ask = _inputs(["YES", "studio_1", "10m", "yes", "research body", "yes", str(script), "Elias Voss", "voice_d33f7035117f4055b1d46eb150234d6a", "no", "audio", "yes", "no", "save"])
    session = episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    calls = [call[0] for call in fake.calls]
    assert calls.index("research-ingest") < calls.index("research-show") < calls.index("episode-create")
    assert session.validation_evidence["accepted_research"]["lifecycle_state"] == "accepted"


def test_already_accepted_research_is_reused_on_resume(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    session = episode.EpisodeSession(session_id="session_accepted", clonecast_repo_path=str(repo.resolve()))
    session.selected_studio_show = "studio_1"
    session.requested_duration_seconds = 600
    session.research_source = {"kind": "pasted_text", "sha256": "r"}
    session.clonecast_episode_identifiers["research_id"] = "research_1"
    episode.save_session(session)
    fake = FakeCloneCast(repo)
    ask = _inputs(["YES", "yes", "Host: Approved.", "Elias Voss", "voice_d33f7035117f4055b1d46eb150234d6a", "no", "audio", "no"])
    completed = episode.run_guided_episode_build(str(repo), resume="session_accepted", input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    assert ["research-ingest"] not in fake.calls
    assert completed.clonecast_episode_identifiers["research_id"] == "research_1"


def test_duplicate_research_resolves_to_existing_accepted_item(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    fake = DuplicateResearchCloneCast(repo)
    ask = _inputs(["YES", "studio_1", "10m", "yes", "research body", "yes", str(script), "Elias Voss", "voice_d33f7035117f4055b1d46eb150234d6a", "no", "audio", "yes", "no", "save"])
    session = episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    assert session.clonecast_episode_identifiers["imported_research_id"] == "research_new"
    assert session.clonecast_episode_identifiers["research_id"] == "research_existing"
    create = next(call for call in fake.calls if call[0] == "episode-create")
    assert create[create.index("--research-id") + 1] == "research_existing"


def test_failed_acceptance_preserves_resumable_state(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    session = episode.EpisodeSession(session_id="session_fail", clonecast_repo_path=str(repo.resolve()))
    session.selected_studio_show = "studio_1"
    session.requested_duration_seconds = 600
    session.research_source = {"kind": "file", "path": str(tmp_path / "research.txt"), "sha256": "r"}
    session.clonecast_episode_identifiers["imported_research_id"] = "research_processing"
    session.script_source = {"kind": "file", "path": str(script)}
    session.script_checksum = episode.checksum_file(script)
    session.imported_script_checksum = session.script_checksum
    session.script_preserved_byte_for_byte = True
    session.selected_host = "Elias Voss"
    session.selected_voices = {"host": "voice_d33f7035117f4055b1d46eb150234d6a"}
    session.guests_or_callers = "no"
    session.media_mode = "audio-only"
    episode.save_session(session)
    fake = FailingAcceptanceCloneCast(repo)
    ask = _inputs(["YES"])
    with pytest.raises(episode.EpisodeBuildError, match="research-recover"):
        episode.run_guided_episode_build(str(repo), resume="session_fail", input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    saved = episode.load_session("session_fail")
    assert saved.failed_stage == "research_acceptance"
    assert saved.selected_host == "Elias Voss"
    assert saved.selected_voices["host"] == "voice_d33f7035117f4055b1d46eb150234d6a"
    assert saved.script_checksum == episode.checksum_file(script)
    assert not any(call[0] == "episode-create" for call in fake.calls)


def test_retry_reuses_existing_episode_and_does_not_create_duplicate(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    managed = session_data_dir / "managed-script.txt"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(script.read_bytes())
    session = episode.EpisodeSession(session_id="session_retry", clonecast_repo_path=str(repo.resolve()))
    session.selected_studio_show = "studio_1"
    session.requested_duration_seconds = 600
    session.research_source = {"kind": "pasted_text", "sha256": "r"}
    session.clonecast_episode_identifiers.update({"research_id": "research_1", "episode_id": "episode_existing", "script_id": "script_1"})
    session.script_source = {"kind": "file", "path": str(script)}
    session.script_checksum = episode.checksum_file(script)
    session.imported_script_checksum = session.script_checksum
    session.script_preserved_byte_for_byte = True
    session.artifact_paths["managed_script"] = str(managed)
    session.selected_host = "Elias Voss"
    session.selected_voices = {"host": "voice_d33f7035117f4055b1d46eb150234d6a"}
    session.guests_or_callers = "no"
    session.media_mode = "audio-only"
    episode.save_session(session)
    fake = FakeCloneCast(repo)
    ask = _inputs(["YES", "yes", "no", "save"])
    completed = episode.run_guided_episode_build(str(repo), resume="session_retry", input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    assert completed.clonecast_episode_identifiers["episode_id"] == "episode_existing"
    assert not any(call[0] == "episode-create" for call in fake.calls)
    assert not any(call[0] == "episode-script-import-approved" for call in fake.calls)


def _saved_ready_to_render_session(repo: Path, script: Path, *, voice_profile_id: str) -> episode.EpisodeSession:
    managed = episode.managed_source_dir() / f"session_resume_voice-{script.name}"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(script.read_bytes())
    session = episode.EpisodeSession(session_id="session_resume_voice", clonecast_repo_path=str(repo.resolve()))
    session.selected_studio_show = "studio_1"
    session.requested_duration_seconds = 600
    session.research_source = {"kind": "pasted_text", "sha256": "r"}
    session.clonecast_episode_identifiers.update(
        {"research_id": "research_1", "episode_id": "episode_existing", "script_id": "script_1"}
    )
    session.script_source = {"kind": "file", "path": str(script)}
    session.script_checksum = episode.checksum_file(script)
    session.imported_script_checksum = session.script_checksum
    session.script_preserved_byte_for_byte = True
    session.artifact_paths["managed_script"] = str(managed)
    session.selected_host = "Elias Voss"
    session.selected_voices = {"host": voice_profile_id}
    session.guests_or_callers = "no"
    session.media_mode = "audio-only"
    episode.save_session(session)
    return session


def test_resume_reuses_identical_existing_voice_assignment_and_continues_to_speech_render(
    session_data_dir, tmp_path
):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    voice_id = "voice_d33f7035117f4055b1d46eb150234d6a"
    _saved_ready_to_render_session(repo, script, voice_profile_id=voice_id)
    fake = FakeCloneCast(repo)
    fake.voice_assignments["script_1"] = [
        {
            "assignment_id": "assign_existing",
            "script_id": "script_1",
            "speaker": "Host",
            "voice_profile_id": voice_id,
        }
    ]
    ask = _inputs(["YES", "yes", "no", "save"])

    completed = episode.run_guided_episode_build(
        str(repo),
        resume="session_resume_voice",
        input_func=ask,
        output=lambda _: None,
        clonecast_factory=lambda _: fake,
    )

    assert completed.completed_stage == "listening_gate"
    assert completed.validation_evidence["voice_assignment"]["status"] == "already_assigned"
    assert not any(call[0] == "script-voice-assign" for call in fake.calls)
    assert len(fake.voice_assignments["script_1"]) == 1
    assert any(call[0] == "speech-render" for call in fake.calls)


def test_resume_stops_on_conflicting_existing_voice_assignment(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    requested_voice = "voice_d33f7035117f4055b1d46eb150234d6a"
    _saved_ready_to_render_session(repo, script, voice_profile_id=requested_voice)
    fake = FakeCloneCast(repo)
    fake.voice_assignments["script_1"] = [
        {
            "assignment_id": "assign_existing",
            "script_id": "script_1",
            "speaker": "Host",
            "voice_profile_id": "voice_different_existing",
        }
    ]
    ask = _inputs(["YES", "yes"])

    with pytest.raises(episode.EpisodeBuildError, match="different voice assigned"):
        episode.run_guided_episode_build(
            str(repo),
            resume="session_resume_voice",
            input_func=ask,
            output=lambda _: None,
            clonecast_factory=lambda _: fake,
        )

    assert not any(call[0] == "script-voice-assign" for call in fake.calls)
    assert not any(call[0] == "speech-render" for call in fake.calls)
    assert len(fake.voice_assignments["script_1"]) == 1


def test_15_minute_speech_render_timeout_exceeds_old_fixed_timeout(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "long-script.txt"
    script.write_text("word " * 3000, encoding="utf-8")
    session = episode.EpisodeSession(session_id="session_timeout_calc", clonecast_repo_path=str(repo.resolve()))
    session.requested_duration_seconds = 900
    session.artifact_paths["managed_script"] = str(script)
    episode.save_session(session)

    timeout = episode.calculate_speech_render_timeout(session)

    assert timeout > 1800
    assert session.requested_duration_seconds == 900


def _write_sleeping_clonecast_cli(repo: Path, *, sleep_seconds: float) -> None:
    (repo / "src" / "clonecast" / "cli.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "import time",
                f"time.sleep({sleep_seconds!r})",
                "if sys.argv[1:] and sys.argv[1] == 'speech-render':",
                "    print(json.dumps({'job': {'job_id': 'speech_cli'}, 'segments': []}))",
                "else:",
                "    print('{}')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_healthy_long_running_worker_emits_heartbeat_and_completes(tmp_path):
    repo = _repo(tmp_path)
    _write_sleeping_clonecast_cli(repo, sleep_seconds=1.2)
    cli = episode.CloneCastCLI(repo)
    heartbeats = []

    result = cli.checked_monitored(
        ["speech-render", "--script-id", "script_1"],
        timeout=5,
        heartbeat_interval=1,
        heartbeat=heartbeats.append,
    )

    assert result.json_data["job"]["job_id"] == "speech_cli"
    assert heartbeats


def test_real_worker_timeout_kills_process_and_reports_failure(tmp_path):
    repo = _repo(tmp_path)
    _write_sleeping_clonecast_cli(repo, sleep_seconds=5)
    cli = episode.CloneCastCLI(repo)

    with pytest.raises(episode.EpisodeBuildError, match="timed out during speech-render"):
        cli.checked_monitored(
            ["speech-render", "--script-id", "script_1"],
            timeout=1,
            heartbeat_interval=1,
            heartbeat=lambda _: None,
        )


class SpeechFailureCleanupCloneCast(FakeCloneCast):
    def checked_monitored(self, args, *, timeout, heartbeat_interval, heartbeat):
        self.calls.append(args)
        heartbeat(heartbeat_interval)
        raise episode.EpisodeBuildError("Chatterbox lifecycle worker response timed out")


def test_speech_failure_records_cleanup_and_vram_release(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    session = episode.EpisodeSession(session_id="session_cleanup", clonecast_repo_path=str(repo.resolve()))
    session.requested_duration_seconds = 900
    session.artifact_paths["managed_script"] = str(script)
    episode.save_session(session)
    fake = SpeechFailureCleanupCloneCast(repo)

    with pytest.raises(episode.EpisodeBuildError, match="worker response timed out"):
        episode._render_speech(session, fake, script_id="script_1", output=lambda _: None)

    saved = episode.load_session("session_cleanup")
    assert saved.failed_stage == "speech_render"
    assert saved.validation_evidence["speech_render_heartbeat"]["status"] == "running"
    cleanup = saved.validation_evidence["speech_failure_cleanup"]
    assert cleanup["attempted"] is True
    assert cleanup["vram_release_confirmed"] is True


def test_resume_after_speech_timeout_preserves_inputs_and_retries_from_speech(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved script body.\n", encoding="utf-8")
    failures_remaining = {"count": 1}
    instances = []

    class OneTimeoutThenSuccess(FakeCloneCast):
        def checked_monitored(self, args, *, timeout, heartbeat_interval, heartbeat):
            self.calls.append(args)
            if failures_remaining["count"]:
                failures_remaining["count"] -= 1
                heartbeat(heartbeat_interval)
                raise episode.EpisodeBuildError("Chatterbox lifecycle worker response timed out")
            return self.checked(args)

    def factory(path):
        fake = OneTimeoutThenSuccess(path)
        instances.append(fake)
        return fake

    ask = _inputs([
        "YES",
        "studio_1",
        "15m",
        "yes",
        "research body",
        "yes",
        str(script),
        "Elias Voss",
        "voice_d33f7035117f4055b1d46eb150234d6a",
        "no",
        "audio",
        "yes",
    ])
    with pytest.raises(episode.EpisodeBuildError, match="worker response timed out"):
        episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=factory)

    saved_path = next((session_data_dir / episode.SESSION_DIRNAME).glob("acce_*.json"))
    failed = episode.load_session(saved_path.stem)
    assert failed.failed_stage == "speech_render"
    assert failed.selected_studio_show == "studio_1"
    assert failed.requested_duration_seconds == 900
    assert failed.clonecast_episode_identifiers["research_id"] == "research_1"
    assert failed.script_preserved_byte_for_byte is True
    assert failed.selected_host == "Elias Voss"
    assert failed.selected_voices["host"] == "voice_d33f7035117f4055b1d46eb150234d6a"

    ask_resume = _inputs(["YES", "yes", "no", "save"])
    completed = episode.run_guided_episode_build(
        str(repo),
        resume=failed.session_id,
        input_func=ask_resume,
        output=lambda _: None,
        clonecast_factory=factory,
    )

    assert completed.completed_stage == "listening_gate"
    assert completed.failed_stage is None
    assert completed.selected_studio_show == "studio_1"
    assert completed.requested_duration_seconds == 900
    assert completed.clonecast_episode_identifiers["research_id"] == "research_1"
    assert completed.script_preserved_byte_for_byte is True
    assert completed.selected_host == "Elias Voss"
    assert completed.selected_voices["host"] == "voice_d33f7035117f4055b1d46eb150234d6a"
    second_calls = instances[-1].calls
    assert not any(call[0] == "episode-create" for call in second_calls)
    assert not any(call[0] == "episode-script-import-approved" for call in second_calls)
    assert any(call[0] == "speech-render" for call in second_calls)


def _write_progress_reporting_clonecast_cli(repo: Path, *, items_completed: int, item_count: int) -> None:
    (repo / "src" / "clonecast" / "cli.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "argv = sys.argv[1:]",
                "if argv and argv[0] == 'speech-render-list':",
                "    print(json.dumps([{'job_id': 'speech_progress_job', 'status': 'rendering', 'updated_at': '2026-01-01T00:00:00+00:00', 'idempotency_key': 'x'}]))",
                "elif argv and argv[0] == 'speech-render-segments':",
                f"    completed = [{{'status': 'completed', 'order_index': i}} for i in range({items_completed})]",
                f"    remaining = [{{'status': 'rendering' if i == {items_completed} else 'pending', 'order_index': i}} for i in range({items_completed}, {item_count})]",
                "    print(json.dumps(completed + remaining))",
                "else:",
                "    print('{}')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_poll_speech_progress_reports_real_segment_counts_from_cli(tmp_path):
    repo = _repo(tmp_path)
    _write_progress_reporting_clonecast_cli(repo, items_completed=3, item_count=7)
    cli = episode.CloneCastCLI(repo)
    session = episode.EpisodeSession(session_id="session_progress", clonecast_repo_path=str(repo.resolve()))
    session.clonecast_episode_identifiers["script_id"] = "script_1"

    progress = episode._poll_speech_progress(cli, session)

    assert progress["items_completed"] == 3
    assert progress["item_count"] == 7
    assert progress["current_segment_order_index"] == 3
    assert progress["job_id"] == "speech_progress_job"


def test_poll_speech_progress_returns_empty_without_a_script_id():
    session = episode.EpisodeSession(session_id="session_no_script", clonecast_repo_path="/tmp/x")
    assert episode._poll_speech_progress(episode.CloneCastCLI(Path("/tmp/x")), session) == {}


def test_poll_speech_progress_is_best_effort_and_never_raises(tmp_path):
    repo = _repo(tmp_path)
    # cli.py that always crashes - the poll must swallow this, not blow up
    # the real heartbeat it's enriching.
    (repo / "src" / "clonecast" / "cli.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    cli = episode.CloneCastCLI(repo)
    session = episode.EpisodeSession(session_id="session_crash", clonecast_repo_path=str(repo.resolve()))
    session.clonecast_episode_identifiers["script_id"] = "script_1"

    assert episode._poll_speech_progress(cli, session) == {}


def test_heartbeat_uses_real_segment_progress_when_available(tmp_path):
    repo = _repo(tmp_path)
    _write_progress_reporting_clonecast_cli(repo, items_completed=2, item_count=5)
    _write_sleeping_clonecast_cli_with_progress(repo, sleep_seconds=1.2)
    cli = episode.CloneCastCLI(repo)
    session = episode.EpisodeSession(session_id="session_hb_progress", clonecast_repo_path=str(repo.resolve()))
    session.requested_duration_seconds = 900
    session.clonecast_episode_identifiers["script_id"] = "script_1"
    episode.save_session(session)
    lines: list[str] = []

    episode._render_speech(session, cli, script_id="script_1", output=lines.append)

    assert any("segment 2 of 5" in line for line in lines)
    saved = episode.load_session("session_hb_progress")
    assert saved.validation_evidence["speech_render_heartbeat"]["items_completed"] == 2
    assert saved.validation_evidence["speech_render_heartbeat"]["item_count"] == 5


def _write_sleeping_clonecast_cli_with_progress(repo: Path, *, sleep_seconds: float) -> None:
    # Same as _write_sleeping_clonecast_cli, but the wrapper script must
    # still answer speech-render-list/speech-render-segments (used by the
    # heartbeat's progress poll, which runs the *same* cli.py) while the
    # main speech-render invocation is asleep - that's a separate process
    # invocation, so this just needs both branches in one script.
    (repo / "src" / "clonecast" / "cli.py").write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "import time",
                "argv = sys.argv[1:]",
                "if argv and argv[0] == 'speech-render-list':",
                "    print(json.dumps([{'job_id': 'speech_progress_job', 'status': 'rendering', 'updated_at': 'x', 'idempotency_key': 'x'}]))",
                "elif argv and argv[0] == 'speech-render-segments':",
                "    print(json.dumps([{'status': 'completed', 'order_index': 0}, {'status': 'completed', 'order_index': 1}, {'status': 'rendering', 'order_index': 2}, {'status': 'pending', 'order_index': 3}, {'status': 'pending', 'order_index': 4}]))",
                "elif argv and argv[0] == 'speech-render':",
                f"    time.sleep({sleep_seconds!r})",
                "    print(json.dumps({'job': {'job_id': 'speech_cli'}, 'segments': []}))",
                "else:",
                "    print('{}')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_friendly_error_message_hides_raw_dump_but_keeps_it_in_technical_detail():
    from app import chat_controller as controller

    raw = (
        "CloneCast command failed: speech-render --script-id script_1\n"
        "stdout:\n(very long real stdout dump)\n"
        "stderr:\nChatterbox lifecycle worker response timed out\n" + ("x" * 3000)
    )
    friendly = controller._friendly_episode_error_message(raw)

    assert len(friendly) < 200
    assert "stdout" not in friendly
    assert "x" * 100 not in friendly
    assert "resume" in friendly.lower() or "regenerate" in friendly.lower()


def test_friendly_error_message_falls_back_to_short_first_line_for_unknown_errors():
    from app import chat_controller as controller

    friendly = controller._friendly_episode_error_message("Some totally new CloneCast failure text")
    assert "Some totally new CloneCast failure text" in friendly
    assert len(friendly) < 250


def test_voice_profile_id_is_passed_and_host_name_is_not_used(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    fake = FakeCloneCast(repo)
    ask = _inputs(["YES", "studio_1: Studio One", "10m", "yes", "research body", "yes", str(script), "Elias Voss", "voice_d33f7035117f4055b1d46eb150234d6a", "no", "audio", "yes", "no", "save"])
    session = episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    assign = next(call for call in fake.calls if call[0] == "script-voice-assign")
    assert assign[assign.index("--voice-profile-id") + 1] == "voice_d33f7035117f4055b1d46eb150234d6a"
    assert "Elias Voss" not in assign
    create = next(call for call in fake.calls if call[0] == "episode-create")
    assert create[create.index("--title") + 1] == "studio_1 Guided Episode"
    assert session.guests_or_callers == "no"
    assert session.owner_approval_status == "publishing_locked"


def test_host_display_name_cannot_be_voice_profile_id(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    ask = _inputs(["YES", "studio_1", "10m", "yes", "research body", "yes", str(script), "Elias Voss", "Elias Voss"])
    with pytest.raises(episode.EpisodeBuildError, match="host display name"):
        episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=FakeCloneCast)


def test_missing_research_blocks_before_generation(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    ask = _inputs(["YES", "studio_1", "10m", "no"])
    with pytest.raises(episode.EpisodeBuildError, match="research is required"):
        episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=FakeCloneCast)


def test_research_handoff_wraps_single_line_body_for_clonecast(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    script = tmp_path / "script.txt"
    script.write_text("Host: Approved.\n", encoding="utf-8")
    fake = FakeCloneCast(repo)
    ask = _inputs(["YES", "studio_1", "10m", "yes", "single line research", "yes", str(script), "Larry", "voice_host", "no", "audio", "no"])
    episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=lambda _: fake)
    ingest = next(call for call in fake.calls if call[0] == "research-ingest")
    payload = json.loads(Path(ingest[1]).read_text(encoding="utf-8"))
    assert payload["title"] == "AutoCorp Guided Research"
    assert payload["body"] == "single line research"
    assert payload["tags"] == ["autocorp-guided-episode"]


def test_missing_script_blocks_before_generation(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    ask = _inputs(["YES", "studio_1", "10m", "yes", "research body", "no"])
    with pytest.raises(episode.EpisodeBuildError, match="final approved script is required"):
        episode.run_guided_episode_build(str(repo), input_func=ask, output=lambda _: None, clonecast_factory=FakeCloneCast)


def test_invalid_clonecast_repository_rejected(tmp_path):
    with pytest.raises(episode.EpisodeBuildError, match="invalid CloneCast repository"):
        episode.CloneCastCLI(tmp_path).validate_repo()


def test_clonecast_command_failure_propagates(tmp_path):
    repo = _repo(tmp_path)

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, "", "boom")

    cli = episode.CloneCastCLI(repo, runner=runner)
    with pytest.raises(episode.EpisodeBuildError, match="stderr:\nboom"):
        cli.checked(["config-check"])


def test_resume_after_interruption_and_idempotent_session_save(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    session = episode.EpisodeSession(session_id="session_1", clonecast_repo_path=str(repo.resolve()), completed_stage="duration_selected")
    first = episode.save_session(session).read_text(encoding="utf-8")
    second = episode.save_session(session).read_text(encoding="utf-8")
    assert episode.load_session("session_1").completed_stage == "duration_selected"
    assert '"session_id": "session_1"' in first
    assert '"session_id": "session_1"' in second


def test_approval_requires_completed_audio(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    session = episode.EpisodeSession(session_id="session_2", clonecast_repo_path=str(repo.resolve()))
    episode.save_session(session)
    with pytest.raises(episode.EpisodeBuildError, match="completed artifact"):
        episode.approve_for_publishing("session_2")


def test_explicit_approval_unlocks_eligibility_but_does_not_publish(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    final_audio = tmp_path / "final.mp3"
    final_audio.write_bytes(b"audio")
    session = episode.EpisodeSession(session_id="session_3", clonecast_repo_path=str(repo.resolve()))
    session.artifact_paths["final_audio"] = str(final_audio)
    episode.save_session(session)
    approved = episode.approve_for_publishing("session_3", owner="Larry")
    assert approved.owner_approval_status == "publishing_eligible"
    assert "publication" not in "\n".join(str(c) for c in approved.clonecast_commands)


def test_publishing_commands_are_blocked(tmp_path):
    repo = _repo(tmp_path)
    cli = episode.CloneCastCLI(repo, runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "{}", ""))
    with pytest.raises(episode.EpisodeBuildError, match="blocked a publishing command"):
        cli.run(["publication-publish", "pub_1"])


def test_regenerating_selected_section_does_not_rebuild_approved_sections(session_data_dir, tmp_path):
    repo = _repo(tmp_path)
    session = episode.EpisodeSession(session_id="session_4", clonecast_repo_path=str(repo.resolve()))
    session.validation_evidence["approved_sections"] = ["ok_1"]
    session.validation_evidence["failed_sections"] = ["bad_1"]
    episode.save_session(session)
    updated = episode.regenerate_section("session_4", "bad_1")
    assert updated.validation_evidence["regeneration_requests"][0]["section_id"] == "bad_1"
    with pytest.raises(episode.EpisodeBuildError, match="already approved"):
        episode.regenerate_section("session_4", "ok_1")


def test_no_direct_production_database_writes():
    source = Path(episode.__file__).read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "connect_database" not in source
    assert "UPDATE " not in source
    assert "INSERT " not in source


def test_cmd_episode_build_reports_errors(capsys, monkeypatch):
    monkeypatch.setattr(episode, "run_guided_episode_build", lambda *a, **k: (_ for _ in ()).throw(episode.EpisodeBuildError("failed")))
    rc = autocorp.cmd_episode_build(argparse.Namespace(repo="/tmp/x", resume=None, approve=None, reject=None, regenerate_section=None))
    assert rc == 2
    assert "Episode Build Error: failed" in capsys.readouterr().err

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
        if args and args[0] == "episode-create":
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
        if args and args[0] == "script-voice-assign":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"speaker": "Host"})
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
            "episode-create",
            "episode-script-import-approved",
            "script-voice-assign",
            "speech-provider-check",
            "speech-render",
            "speech-render-validate",
            "episode-audio-assemble",
            "episode-audio-validate",
            "episode-audio-master",
        }


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

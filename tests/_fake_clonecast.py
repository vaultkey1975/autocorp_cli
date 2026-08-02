"""Shared fake CloneCast CLI double for AutoCorp Chat App tests.

Mirrors the fixture used by tests/test_guided_clonecast_episode_operator.py
but also answers the read-only listing commands (``radio-studio-list``,
``voice-list``) the web app's friendly-label layer needs, using the exact
studio/voice fixture named in the Phase 1 spec (Shadow Frequency / Elias Voss
/ the approved Larry voice).
"""

from __future__ import annotations

from pathlib import Path

from brains import guided_clonecast_episode as episode

STUDIO_ID = "studio_c7599bb4733e438d9f1926e0e4ad6111"
STUDIO_DISPLAY_NAME = "Shadow Frequency"
VOICE_LARRY_APPROVED = "voice_d33f7035117f4055b1d46eb150234d6a"
VOICE_LARRY_DRAFT = "voice_cb61cd4575a44e3ba2bfac8d9a3acd74"
VOICE_DANIEL_APPROVED = "voice_5809e994d6c4411c8dd8e0725c24b0a5"


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "clonecast"
    (repo / "src" / "clonecast").mkdir(parents=True)
    (repo / "src" / "clonecast" / "cli.py").write_text("# cli\n", encoding="utf-8")
    (repo / "migrations").mkdir()
    return repo


class FakeCloneCastCLI(episode.CloneCastCLI):
    def __init__(self, repo: Path):
        self.repo = repo
        self.calls: list[list[str]] = []
        self.research_states = {"research_1": "accepted"}
        self.duplicate_of: dict[str, str] = {}

    def validate_repo(self):
        super().validate_repo()

    def discover_commands(self):
        return {
            "radio-studio-list",
            "voice-list",
            "research-ingest",
            "research-show",
            "research-recover",
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

    def checked(self, args, *, input_text=None):
        self.calls.append(args)
        head = args[0] if args else ""

        if args == ["config-check"]:
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"db_path": str(self.repo / "db" / "cloneshow.db")})

        if args == ["radio-studio-list"]:
            data = [{"studio_id": STUDIO_ID, "display_name": STUDIO_DISPLAY_NAME, "lifecycle_status": "approved"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)

        if head == "voice-list":
            data = [
                {
                    "voice_profile_id": VOICE_DANIEL_APPROVED,
                    "display_name": "Daniel",
                    "lifecycle_status": "approved",
                    "stable_name": "daniel-caller.v1",
                    "version_label": "daniel-production-v1",
                    "version_number": 1,
                },
                {
                    "voice_profile_id": VOICE_LARRY_DRAFT,
                    "display_name": "Larry",
                    "lifecycle_status": "draft",
                    "stable_name": "larry.v1",
                    "version_label": "larry-production-v1",
                    "version_number": 1,
                },
                {
                    "voice_profile_id": VOICE_LARRY_APPROVED,
                    "display_name": "Larry",
                    "lifecycle_status": "approved",
                    "stable_name": "larry.v2",
                    "version_label": "larry-production-v1",
                    "version_number": 2,
                },
            ]
            if "--status" in args:
                status = args[args.index("--status") + 1]
                data = [d for d in data if d["lifecycle_status"] == status]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)

        if head == "research-ingest":
            data = [{"status": "accepted", "research_id": "research_1"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)

        if head == "research-show":
            research_id = args[1]
            data = {
                "research_id": research_id,
                "lifecycle_state": self.research_states.get(research_id, "accepted"),
                "duplicate_of_research_id": self.duplicate_of.get(research_id),
                "content_hash": "c" * 64,
                "current_path": str(self.repo / "research.txt"),
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "research-recover":
            research_id = args[args.index("--research-id") + 1]
            self.research_states[research_id] = "accepted"
            data = [{"status": "accepted", "research_id": research_id}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)

        if head == "episode-create":
            data = {"episode_id": "episode_1", "idempotent": False}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "episode-script-import-approved":
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

        if head == "script-voice-assign":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"speaker": "Host"})

        if head == "speech-provider-check":
            data = {"available": True, "provider": "chatterbox-turbo", "preflight": {"may_begin": True, "free_vram_mib": 12000}}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "speech-render":
            data = {"job": {"job_id": "speech_1"}, "segments": [{"segment_render_id": "seg_1", "output_path": str(self.repo / "segment.wav")}]}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "speech-render-validate":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"valid": True})

        if head == "episode-audio-assemble":
            final = self.repo / "episode.mp3"
            final.write_bytes(b"x" * 2048)
            data = {
                "job": {"job_id": "audio_1"},
                "outputs": [{"output_type": "mp3", "path": str(final), "duration_seconds": 42.0, "file_size_bytes": final.stat().st_size, "container": "mp3", "codec": "mp3", "sha256": "a" * 64}],
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "episode-audio-validate":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"valid": True})

        if head == "episode-audio-master":
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

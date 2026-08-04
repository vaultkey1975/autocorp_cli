"""Shared fake CloneCast CLI double for AutoCorp Chat App tests.

Mirrors the fixture used by tests/test_guided_clonecast_episode_operator.py
but also answers the read-only listing commands (``radio-studio-list``,
``voice-list``) the web app's friendly-label layer needs, using the exact
studio/voice fixture named in the Phase 1 spec (Shadow Frequency / Elias Voss
/ the approved Larry voice).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from brains import guided_clonecast_episode as episode

_BARE_STRUCTURAL_LABELS = {"OPEN", "COLD OPEN", "INTRO", "OUTRO", "END"}
_NUMBERED_HEADING_RE = re.compile(r"^(chapter|section|part|segment)\s+\d+\s*:?\s*(?P<title>.*)$", re.IGNORECASE)


def _fake_speech_text_preview(text: str) -> dict:
    """Small, deliberately-not-production-grade stand-in for CloneCast's real
    canonical transform, used only so this fake CLI double returns a
    real-shaped `speech-text-preview` payload. The transform's actual
    correctness is exercised exhaustively against the real implementation in
    the CloneCast repository's own test suite, not here."""
    out_lines: list[str] = []
    removed_headings: list[str] = []
    retained_titles: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.upper() in _BARE_STRUCTURAL_LABELS:
            removed_headings.append(stripped)
            continue
        match = _NUMBERED_HEADING_RE.match(stripped) if stripped else None
        if match:
            title = match.group("title").strip()
            if title:
                out_lines.append(title)
                retained_titles.append(title)
            else:
                removed_headings.append(stripped)
            continue
        out_lines.append(line)
    speech_text = "\n".join(out_lines).strip()
    return {
        "approved_script_checksum": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "rendered_text_checksum": hashlib.sha256(speech_text.encode("utf-8")).hexdigest(),
        "transformation_version": "speech-text-transform-v2",
        "speech_text": speech_text,
        "removed_headings": removed_headings,
        "retained_titles": retained_titles,
        "production_cues": [],
        "uncertainty_warnings": [],
        "segment_plan": [
            {
                "segment_id": "seg_1",
                "order_index": 0,
                "speaker": "Host",
                "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "rendered_text_sha256": hashlib.sha256(speech_text.encode("utf-8")).hexdigest(),
            }
        ],
    }

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
        self.voice_assignments: dict[str, list[dict[str, str]]] = {}
        self.imported_scripts: dict[str, str] = {}
        self.invalidated_segments: list[tuple[str, str]] = []
        # STUDIO_ID is "the exact studio/voice fixture named in the Phase 1
        # spec" (see module docstring) and has always had a professional
        # radio-host delivery default in practice; represent that here as a
        # configured story profile (the real-world equivalent of running
        # `radio-studio-story-profile-set`) rather than as code that
        # name-matches "Shadow Frequency".
        self.story_profiles: dict[str, dict] = {STUDIO_ID: {"default_delivery_preset_id": "dvpreset_radio_host_v1"}}

    def validate_repo(self):
        super().validate_repo()

    def discover_commands(self):
        return {
            "radio-studio-list",
            "radio-studio-story-profile-show",
            "voice-list",
            "research-ingest",
            "research-show",
            "research-recover",
            "episode-create",
            "episode-script-import-approved",
            "speech-text-preview",
            "script-voice-list",
            "script-voice-assign",
            "script-voice-unassign",
            "voice-delivery-preset-show",
            "speech-provider-check",
            "speech-render",
            "speech-render-validate",
            "speech-render-invalidate-segment",
            "episode-audio-assemble",
            "episode-audio-validate",
            "episode-audio-master",
        }

    def checked(self, args, *, input_text=None):
        self.calls.append(args)
        head = args[0] if args else ""

        if args == ["config-check"]:
            data = {"db_path": str(self.repo / "db" / "cloneshow.db")}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if args == ["radio-studio-list"]:
            data = [{"studio_id": STUDIO_ID, "display_name": STUDIO_DISPLAY_NAME, "lifecycle_status": "approved"}]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "[]", "", data)

        if head == "radio-studio-story-profile-show":
            studio_id = args[1]
            profile = self.story_profiles.get(studio_id)
            if profile is None:
                return episode.CloneCastResult(
                    ["python", "-m", "clonecast.cli", *args], 1, "", "no story profile configured", None
                )
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", profile)

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
            self.imported_scripts["script_1"] = script_file.read_text(encoding="utf-8")
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

        if head == "speech-text-preview":
            script_id = args[args.index("--script-id") + 1]
            text = self.imported_scripts.get(script_id, "")
            data = {"script_id": script_id, "episode_id": "episode_1", **_fake_speech_text_preview(text)}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "script-voice-list":
            return episode.CloneCastResult(
                ["python", "-m", "clonecast.cli", *args],
                0,
                "[]",
                "",
                self.voice_assignments.get(args[1], []),
            )

        if head == "script-voice-assign":
            script_id = args[args.index("--script-id") + 1]
            speaker = args[args.index("--speaker") + 1]
            voice_profile_id = args[args.index("--voice-profile-id") + 1]
            delivery_preset_id = (
                args[args.index("--delivery-preset-id") + 1] if "--delivery-preset-id" in args else None
            )
            assignment = {
                "assignment_id": f"assign_{len(self.voice_assignments.get(script_id, [])) + 1}",
                "script_id": script_id,
                "speaker": speaker,
                "voice_profile_id": voice_profile_id,
                "delivery_preset_id": delivery_preset_id,
                "generation_settings_sha256": "95f3a122ff06ae6a7beced1d88d4abb415045a3f35d2b7dbb41106a2b8c1c656"
                if delivery_preset_id == "dvpreset_radio_host_v1"
                else "184a1bf5c2f194840fa0828ae638469092433d60b723ce727feff07eb0bf3d31",
            }
            existing = next(
                (item for item in self.voice_assignments.get(script_id, []) if item["speaker"] == speaker),
                None,
            )
            if existing:
                existing.update(
                    {
                        "voice_profile_id": voice_profile_id,
                        "delivery_preset_id": assignment["delivery_preset_id"],
                        "generation_settings_sha256": assignment["generation_settings_sha256"],
                    }
                )
                return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", existing)
            self.voice_assignments.setdefault(script_id, []).append(assignment)
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", assignment)

        if head == "script-voice-unassign":
            script_id = args[args.index("--script-id") + 1]
            speaker = args[args.index("--speaker") + 1]
            self.voice_assignments[script_id] = [
                item for item in self.voice_assignments.get(script_id, []) if item["speaker"] != speaker
            ]
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"removed": None})

        if head == "voice-delivery-preset-show":
            preset_id = args[1]
            if preset_id == "dvpreset_radio_host_v1":
                data = {
                    "delivery_preset_id": "dvpreset_radio_host_v1",
                    "stable_name": "radio-host",
                    "display_name": "Radio Host v1",
                    "generation_settings": {
                        "norm_loudness": True,
                        "repetition_penalty": 1.05,
                        "temperature": 0.7,
                        "top_k": 1000,
                        "top_p": 0.9,
                    },
                    "generation_settings_sha256": "95f3a122ff06ae6a7beced1d88d4abb415045a3f35d2b7dbb41106a2b8c1c656",
                }
            else:
                data = {
                    "delivery_preset_id": "dvpreset_natural_v1",
                    "stable_name": "natural",
                    "display_name": "Natural v1",
                    "generation_settings": {
                        "norm_loudness": True,
                        "repetition_penalty": 1.05,
                        "temperature": 0.8,
                        "top_k": 1000,
                        "top_p": 0.95,
                    },
                    "generation_settings_sha256": "184a1bf5c2f194840fa0828ae638469092433d60b723ce727feff07eb0bf3d31",
                }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "speech-provider-check":
            data = {
                "available": True,
                "provider": "chatterbox-turbo",
                "preflight": {"may_begin": True, "free_vram_mib": 12000},
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "speech-render":
            data = {
                "job": {"job_id": "speech_1"},
                "segments": [{"segment_render_id": "seg_1", "segment_id": "seg_1", "output_path": str(self.repo / "segment.wav")}],
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "speech-render-validate":
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {"valid": True})

        if head == "speech-render-invalidate-segment":
            job_id = args[args.index("--job-id") + 1]
            segment_id = args[args.index("--segment-id") + 1]
            self.invalidated_segments.append((job_id, segment_id))
            data = {"job_id": job_id, "segment_id": segment_id, "invalidated": True}
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        if head == "episode-audio-assemble":
            final = self.repo / "episode.mp3"
            final.write_bytes(b"x" * 2048)
            data = {
                "job": {"job_id": "audio_1"},
                "outputs": [
                    {
                        "output_type": "mp3",
                        "path": str(final),
                        "duration_seconds": 42.0,
                        "file_size_bytes": final.stat().st_size,
                        "container": "mp3",
                        "codec": "mp3",
                        "sha256": "a" * 64,
                    }
                ],
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
                    {
                        "artifact_type": "source_mp3",
                        "path": str(raw),
                        "sha256": "a" * 64,
                        "file_size_bytes": raw.stat().st_size,
                        "duration_seconds": 42.0,
                        "container": "mp3",
                        "codec": "mp3",
                    },
                    {
                        "artifact_type": "mastered_mp3",
                        "path": str(mastered),
                        "sha256": "b" * 64,
                        "file_size_bytes": mastered.stat().st_size,
                        "duration_seconds": 42.0,
                        "container": "mp3",
                        "codec": "mp3",
                    },
                ],
                "validation": {
                    "valid": True,
                    "measurements": {"output_i_lufs": -16.0, "output_tp_dbtp": -2.1, "output_lra_lu": 2.5},
                },
            }
            return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", data)

        return episode.CloneCastResult(["python", "-m", "clonecast.cli", *args], 0, "{}", "", {})

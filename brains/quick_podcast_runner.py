#!/usr/bin/env python3
"""
Quick Podcast Runner  (AutoCorp CLI - brains)  [Observability Refactor]
=========================================================================

Real module replacing the previous "python -c '<thousands of lines>'"
embedded-string implementation. Same CloneCast service calls, same
validation thresholds, same retry logic - nothing about *what* this does
or *how well* it generates a podcast has changed. What changed is that this
now runs as a normal importable/inspectable module, invoked as:

    <clonecast-venv-python> -m brains.quick_podcast_runner <args>

instead of being passed as a giant inline string to `python -c`, and it now
prints structured, immediately-flushed progress as it goes (phase
transitions, blueprint-section progress, Ollama retries, per-turn voice
rendering progress, QC check results, package steps, and periodic
elapsed/remaining estimates) instead of staying silent until the very end.

This module MUST run inside CloneCast's own venv (it imports `clonecast.*`,
which only exists there) - it deliberately imports nothing from AutoCorp's
own `brains`/`core` packages beyond the Python standard library, since
CloneCast's venv does not have AutoCorp's third-party dependencies (rich,
requests, etc.) installed.

Communication with the parent AutoCorp process (brains/quick_podcast.py):
  * Every progress line is written to stdout (flushed) AND to the shared
    log file - the parent streams this process's stdout live and forwards
    each line to its own console, so a human watching AutoCorp's terminal
    sees this module's output in real time, not just at exit.
  * On success, the LAST line printed is a single JSON object with
    `"ok": true` and the same result fields the previous embedded-script
    version produced (output_dir, episode_id, package_id, mp3 metadata,
    etc.) - the parent parses this line to build its own final report.
  * On failure, a single JSON object with `"ok": false, "phase", "reason"`
    is printed and the process exits with code 2 - identical contract to
    the previous embedded-script version's `fail()` helper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# Phase taxonomy (UNCHANGED from the previous embedded-script version and
# from brains/quick_podcast.py's own PHASES tuple - preserved exactly so
# existing report structure/behavior does not change).
# --------------------------------------------------------------------------- #
PHASES = (
    "Repository",
    "CloneCast Health",
    "Research",
    "Script",
    "Storytelling",
    "Host Performance",
    "Voice",
    "Artwork",
    "Assembly",
    "Audio QC",
    "Package",
    "Publishing",
)

# Relative work-weight per phase, used only to produce a rough "estimated
# remaining time" - deliberately approximate (see module docstring / Task 10:
# "Estimate is acceptable. It does not need to be exact."). Script generation
# (Ollama) and Voice rendering (Chatterbox) dominate real runtime.
_PHASE_WEIGHTS = {
    "Repository": 1, "CloneCast Health": 3, "Research": 2, "Script": 40,
    "Storytelling": 2, "Host Performance": 1, "Voice": 35, "Artwork": 1,
    "Assembly": 6, "Audio QC": 4, "Package": 4, "Publishing": 1,
}
_TOTAL_WEIGHT = sum(_PHASE_WEIGHTS.values())


class QuickPodcastFailure(RuntimeError):
    """Mirrors the previous embedded script's fail()-then-exit(2) contract."""

    def __init__(self, phase: str, reason: str):
        super().__init__(reason)
        self.phase = phase
        self.reason = reason


# --------------------------------------------------------------------------- #
# Progress / logging
# --------------------------------------------------------------------------- #
class Progress:
    """Prints every line to stdout (flushed immediately) AND to the shared
    persistent log file (line-buffered, flushed immediately) - so both a
    live terminal and `tail -f <log>` see the same output as it happens."""

    def __init__(self, log_path: str, phase_index_start: int = 0):
        self._t0 = time.time()
        # Lets the parent process's own already-completed phases (e.g.
        # "Repository") continue a single coherent [N/12] sequence across
        # the process boundary, rather than each process counting from 1.
        self._phase_index = phase_index_start
        self._weight_done = 0
        # Line-buffered text mode + explicit flush on every write, per the
        # "usable with tail -f" requirement.
        self._log = open(log_path, "a", buffering=1, encoding="utf-8")

    def emit(self, line: str = "") -> None:
        print(line, flush=True)
        self._log.write(line + "\n")
        self._log.flush()

    def close(self) -> None:
        self._log.close()

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def elapsed(self) -> float:
        return time.time() - self._t0

    def _format_hms(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _format_remaining(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"~{m}m {s}s" if m else f"~{s}s"

    def phase_start(self, name: str) -> None:
        self._phase_index += 1
        fraction_done = self._weight_done / _TOTAL_WEIGHT if _TOTAL_WEIGHT else 0
        elapsed = self.elapsed()
        if fraction_done > 0:
            remaining = elapsed / fraction_done * (1 - fraction_done)
        else:
            remaining = 0.0
        dots = "." * max(1, 24 - len(name))
        self.emit(f"[{self._phase_index}/{len(PHASES)}] {name} {dots} START")
        self.emit(f"  Elapsed:            {self._format_hms(elapsed)}")
        self.emit(f"  Current Phase:      {name}")
        if fraction_done > 0:
            self.emit(f"  Estimated Remaining: {self._format_remaining(remaining)}")
        self.emit("")

    def phase_done(self, name: str, status: str = "PASS") -> None:
        self._weight_done += _PHASE_WEIGHTS.get(name, 1)
        dots = "." * max(1, 24 - len(name))
        self.emit(f"[{self._phase_index}/{len(PHASES)}] {name} {dots} {status}")
        self.emit("")


# --------------------------------------------------------------------------- #
# Long-form conversation provider (moved verbatim - same generation logic,
# same retry/continuation behavior, same minimum-word thresholds - only
# progress printing has been added inside the existing loops)
# --------------------------------------------------------------------------- #
class LongformConversationProvider:
    provider_name = "ollama"
    endpoint_identifier = ""

    def __init__(self, inner, target_seconds: int, progress: Progress):
        self.inner = inner
        self.model_name = inner.model_name
        self.endpoint_identifier = inner.endpoint_identifier
        self.target_seconds = target_seconds
        self.minimum_words = max(80, int(target_seconds * 2.5))
        self.progress = progress

    def check(self):
        return self.inner.check()

    def _valid(self, raw):
        try:
            data = json.loads(raw)
        except Exception:
            return False, "response is not JSON"
        turns = data.get("turns") if isinstance(data, dict) else None
        if not turns:
            return False, "response has no turns"
        words = sum(len(str(turn.get("spoken_text", "")).split()) for turn in turns)
        if words < self.minimum_words:
            return False, f"response has {words} words; minimum is {self.minimum_words}"
        if turns[-1].get("turn_type") != "closing":
            return False, "final turn_type is not closing"
        total = sum(float(turn.get("estimated_duration_seconds") or 0) for turn in turns)
        if abs(total - float(data.get("estimated_duration_seconds") or 0)) > 1:
            return False, "turn duration total disagrees with estimated duration"
        return True, ""

    def _first_json_object(self, raw):
        decoder = json.JSONDecoder()
        text = str(raw).strip()
        value, _end = decoder.raw_decode(text)
        return value

    def generate(self, system, user, *, response_schema=None):
        source = self._first_json_object(user)
        conversation = source["conversation"]
        participants = source["participants"]
        blueprint = source["blueprint"]
        host = next(p for p in participants if p["participant_type"] == "host")
        total_duration = int(conversation["target_duration_seconds"])
        target_words = self.minimum_words
        turns = []
        accumulated_words = 0
        turn_seconds = [int(item["expected_duration_seconds"]) for item in blueprint]
        if turn_seconds:
            turn_seconds[-1] += total_duration - sum(turn_seconds)
        section_schema = {
            "type": "object",
            "properties": {"spoken_text": {"type": "string"}},
            "required": ["spoken_text"],
        }
        for index, item in enumerate(blueprint):
            section_t0 = time.time()
            self.progress.emit(f"Blueprint section {index + 1}/{len(blueprint)}")
            self.progress.emit(f"Purpose:\n{item['turn_purpose']}")
            seconds = max(1, turn_seconds[index])
            section_target = max(60, int(target_words * (seconds / total_duration)))
            if index == len(blueprint) - 1:
                section_target = min(section_target, 120)
            parts = []
            attempts = 0
            while sum(len(part.split()) for part in parts) < section_target and attempts < 5:
                attempts += 1
                words_so_far = sum(len(part.split()) for part in parts)
                if attempts > 1:
                    self.progress.emit(f"Retry {attempts}/5")
                    self.progress.emit(f"Reason:\nOnly {words_so_far} words generated")
                    self.progress.emit("Requesting continuation...")
                section_system = (
                    "Return JSON only with one key, spoken_text. "
                    "Write finished professional radio narration for a CloneCast host. "
                    "No placeholders, no stage directions, no bullets, no markdown, no citations you cannot support. "
                    "Use suspense, pacing, emphasis, transitions, and careful uncertainty language."
                )
                section_user = json.dumps(
                    {
                        "show": "Shadow Frequency",
                        "topic": conversation["intended_topic"],
                        "section": item["turn_purpose"],
                        "emotional_direction": item["emotional_direction"],
                        "minimum_words_this_response": min(260, max(90, section_target - words_so_far)),
                        "already_written": " ".join(parts[-1:]),
                        "instruction": "Continue the section without repeating earlier wording. Make it listenable spoken narration.",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                raw_section = self.inner.generate(section_system, section_user, response_schema=section_schema)
                try:
                    spoken = str(self._first_json_object(raw_section).get("spoken_text") or "").strip()
                except Exception:
                    spoken = ""
                if spoken:
                    parts.append(spoken)
            spoken_text = " ".join(parts).strip()
            section_words = len(spoken_text.split())
            accumulated_words += section_words
            self.progress.emit("Completed")
            self.progress.emit(f"Words: {section_words}")
            self.progress.emit(f"Elapsed: {time.time() - section_t0:.1f} sec")
            self.progress.emit("")
            turns.append(
                {
                    "position": index,
                    "participant_id": item["intended_speaker_participant_id"],
                    "addressed_participant_id": item.get("addressed_participant_id"),
                    "turn_type": "closing" if index == len(blueprint) - 1 else "dialogue",
                    "spoken_text": spoken_text,
                    "production_directions": "",
                    "evidence_ids": [],
                    "disclosure": False,
                    "interrupts_position": None,
                    "estimated_duration_seconds": seconds,
                }
            )
        if accumulated_words < target_words:
            raise RuntimeError(f"long-form Ollama generation produced {accumulated_words} words; minimum is {target_words}")
        payload = {
            "conversation_id": conversation["conversation_id"],
            "generation_version": conversation.get("generation_version", 1),
            "estimated_duration_seconds": total_duration,
            "turns": turns,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        ok, reason = self._valid(raw)
        if not ok:
            raise RuntimeError(reason)
        return raw


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _poll_voice_render_progress(db_path: Path, conversation_id: str, generation_id: str,
                                progress: Progress, stop_event: threading.Event) -> None:
    """Background-thread progress poller for voice rendering (Task 5). Reads
    the disposable database through its OWN read-only connection while the
    main thread's synchronous render_conversation() call is in flight -
    the database is already in WAL mode (see clonecast.db.connect_database),
    which supports concurrent readers safely. Never writes anything."""
    import sqlite3

    try:
        total_row = None
        while total_row is None and not stop_event.is_set():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                total_row = conn.execute(
                    "SELECT COUNT(*) FROM ai_conversation_turns WHERE generation_id=?",
                    (generation_id,),
                ).fetchone()
            finally:
                conn.close()
            if not total_row or not total_row[0]:
                total_row = None
                stop_event.wait(0.5)
        total = total_row[0] if total_row else 0
        if total <= 0:
            return
        seen = -1
        while not stop_event.is_set():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM conversation_voice_turn_assets "
                    "WHERE conversation_id=? AND status='completed'",
                    (conversation_id,),
                ).fetchone()
            finally:
                conn.close()
            completed = row[0] if row else 0
            if completed != seen:
                seen = completed
                progress.emit(f"Turn {min(completed, total)} / {total} complete")
            if completed >= total:
                return
            stop_event.wait(1.0)
    except Exception:
        # Progress reporting must never crash or interfere with the actual
        # render_conversation() call running on the main thread.
        pass


def _run(args: argparse.Namespace) -> dict:
    progress = Progress(args.log_file, phase_index_start=args.phase_offset)
    progress.emit("========================================")
    progress.emit("CloneCast Worker (brains.quick_podcast_runner)")
    progress.emit("========================================")
    progress.emit("")

    repo = Path(args.repo)
    disp = Path(args.disp)
    db_path = Path(args.db)
    research_path = Path(args.research)
    output_dir = Path(args.output)
    show = args.show
    topic = args.topic
    duration = args.duration_seconds
    voice_override = (args.voice or "").strip()

    def fail(phase: str, reason) -> None:
        raise QuickPodcastFailure(phase, str(reason))

    # ----------------------------------------------------------------- #
    # CloneCast Health
    # ----------------------------------------------------------------- #
    progress.phase_start("CloneCast Health")
    from clonecast.config import load_settings
    from clonecast.db import connect_database, upgrade_database

    try:
        settings = load_settings(environ=os.environ)
        conn = connect_database(db_path)
        upgrade_database(conn, settings.migrations_path)
    except Exception as exc:
        fail("CloneCast Health", exc)

    try:
        from fastapi.testclient import TestClient
        from clonecast.web_app import create_app
        response = TestClient(create_app(settings)).get("/health")
        if response.status_code != 200:
            fail("CloneCast Health", f"health route returned HTTP {response.status_code}")
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("CloneCast Health", exc)

    from clonecast.script_provider import OllamaProvider
    from clonecast.conversation_voice_service import ConversationVoiceService

    try:
        base_provider = OllamaProvider(
            settings.ollama_endpoint, settings.ollama_model, settings.ollama_timeout_seconds,
            settings.ollama_temperature, settings.conversation_max_response_bytes,
        )
        base_provider.check()
        ConversationVoiceService(conn, settings).provider_check()
        subprocess.run([settings.delivery_ffmpeg_executable, "-version"],
                       capture_output=True, text=True, timeout=10, check=True)
    except Exception as exc:
        fail("CloneCast Health", exc)
    progress.phase_done("CloneCast Health")

    # ----------------------------------------------------------------- #
    # Research + Episode
    # ----------------------------------------------------------------- #
    progress.phase_start("Research")
    from clonecast.research_service import ResearchService
    from clonecast.episode_service import EpisodeService

    provider = LongformConversationProvider(base_provider, duration, progress)
    try:
        research = ResearchService(conn, settings).ingest_one(research_path)
        if research.status != "accepted":
            fail("Research", f"research ingestion returned {research.status}: {research.message}")
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Research", exc)

    try:
        episode_id = EpisodeService(conn).create([research.research_id], topic, "quick-podcast-episode").episode_id
    except Exception as exc:
        fail("Research", exc)
    progress.phase_done("Research")

    # ----------------------------------------------------------------- #
    # Voice (studio/character/voice-assignment setup)
    # ----------------------------------------------------------------- #
    progress.phase_start("Voice")
    from clonecast.radio_studio_service import RadioStudioService

    try:
        radio = RadioStudioService(conn, settings)
        studio = radio.create_studio("quick-podcast-studio", show, "solo_host", "quick-podcast-studio")
        studio_id = studio["studio_id"]
        radio.validate_studio(studio_id)
        radio.review("studio", studio_id, "AutoCorp", "accepted", "Quick podcast validation.")
        radio.approve("studio", studio_id, "AutoCorp")
        radio.activate_studio(studio_id)
        character = radio.create_character(studio_id, "host", "Elias Voss", "host", "quick-podcast-host")
        character_id = character["character_id"]
        radio.validate_character(character_id)
        radio.review("character", character_id, "AutoCorp", "accepted", "Quick podcast validation.")
        radio.approve("character", character_id, "AutoCorp")
        radio.activate_character(character_id)
        if voice_override:
            voice_row = conn.execute(
                "SELECT voice_profile_id FROM voice_profiles WHERE voice_profile_id=? AND lifecycle_status='approved'",
                (voice_override,),
            ).fetchone()
        else:
            voice_row = conn.execute(
                "SELECT voice_profile_id FROM voice_profiles WHERE lifecycle_status='approved' "
                "ORDER BY CASE WHEN lower(display_name) LIKE '%larry%' THEN 0 ELSE 1 END, created_at LIMIT 1"
            ).fetchone()
        if voice_row is None:
            fail("Voice", "no approved CloneCast voice profile is available")
        preset = conn.execute(
            "SELECT preset_id FROM radio_audio_presets WHERE lifecycle_status='active' ORDER BY created_at LIMIT 1"
        ).fetchone()
        radio.assign_voice(character_id, voice_row["voice_profile_id"], purpose="primary",
                           preset_id=preset["preset_id"] if preset else None)
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Voice", exc)
    progress.phase_done("Voice")

    # ----------------------------------------------------------------- #
    # Script: session/segment
    # ----------------------------------------------------------------- #
    progress.phase_start("Script")
    try:
        session = radio.create_session(studio_id, topic, "Quick podcast production validation", duration,
                                       "quick-podcast-session", episode_id=episode_id)
        session_id = session["session_id"]
        radio.configure_session(session_id)
        segment = radio.add_session_segment(session_id, 0, "host_introduction", topic, duration,
                                            "quick-podcast-segment", assigned_character_id=character_id,
                                            assigned_role="host")
        radio.validate_session(session_id)
        radio.review_session(session_id, "AutoCorp", "accepted", "Quick podcast validation.")
        radio.approve_session(session_id, "AutoCorp")
    except Exception as exc:
        fail("Script", f"{exc}\n{traceback.format_exc()}")

    # ----------------------------------------------------------------- #
    # Script: conversation + blueprint + dialogue generation
    # ----------------------------------------------------------------- #
    from clonecast.ai_conversation_service import AIConversationService

    try:
        conversations = AIConversationService(conn, settings, provider)
        conversation = conversations.create_conversation(
            studio_id, session_id, segment["segment_id"], character_id,
            "Produce a complete professional radio podcast episode.",
            topic, duration, "quick-podcast-conversation",
            evidence_requirements="Use the accepted research item and clearly label uncertainty.",
            safety_boundaries="Do not claim unverified folklore as proven fact.",
        )
        cid = conversation["conversation_id"]
        conversations.configure_conversation(cid)
        participants = conversations.snapshot_participants(cid)
        host = next(p["participant_id"] for p in participants if p["participant_type"] == "host")
        target_words = max(80, int(duration * 2.5))
        if duration < 180:
            blueprint = [
                ("opening hook", topic, "calm suspense", max(15, duration // 2), "none"),
                ("closing", topic, "professional sign-off", max(15, duration - max(15, duration // 2)), "hard_close"),
            ]
        else:
            closing_seconds = max(35, int(duration * 0.06))
            section_specs = [
                ("opening hook and stakes", "calm suspense"),
                ("documented setting", "investigative"),
                ("wartime rumor environment", "measured tension"),
                ("the alleged experiment claim", "careful"),
                ("timeline reconstruction", "investigative"),
                ("witness and folklore separation", "reflective"),
                ("scientific plausibility", "measured"),
                ("paper trail and missing evidence", "skeptical"),
                ("why the story endured", "thoughtful"),
                ("public memory and urban legend", "reflective"),
                ("what can be said responsibly", "clear"),
                ("listener synthesis", "calm suspense"),
                ("final turn toward the audience", "professional"),
                ("closing", "professional sign-off"),
            ]
            body = max(25, (duration - closing_seconds) // (len(section_specs) - 1))
            blueprint = [(purpose, topic, emotion, body, "none") for purpose, emotion in section_specs[:-1]]
            blueprint.append((section_specs[-1][0], topic, section_specs[-1][1], closing_seconds, "hard_close"))
        scale = duration / sum(item[3] for item in blueprint)
        for index, (purpose, item_topic, emotion, seconds, closing) in enumerate(blueprint):
            expected_seconds = max(15, int(seconds * scale))
            section_words = max(45, int(target_words * (expected_seconds / duration)))
            if closing == "hard_close":
                section_words = min(section_words, 90)
            conversations.add_blueprint_item(
                cid, index, host, purpose, item_topic, emotion,
                f"Reference the accepted research and distinguish documented facts from disputed claims. "
                f"The spoken_text for this section must contain about {section_words} words of complete "
                f"broadcast narration, with pacing, emphasis, suspense, transitions, and professional radio delivery.",
                expected_seconds, f"quick-podcast-blueprint-{index}", closing_behavior=closing,
            )
        conversations.validate_conversation(cid)
        progress.emit("Rendering script...")
        progress.emit("")
        generation = conversations.generate_dialogue(cid, "quick-podcast-generation")
        turns_for_length = conversations.list_turns(generation["generation_id"])
        generated_words = sum(len(str(row["spoken_text"]).split()) for row in turns_for_length)
        if generated_words < int(target_words * 0.70):
            fail("Script", f"generated script is materially under target length: {generated_words} words for requested {duration} seconds")
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Script", exc)
    progress.phase_done("Script")

    # ----------------------------------------------------------------- #
    # Storytelling
    # ----------------------------------------------------------------- #
    progress.phase_start("Storytelling")
    try:
        conversations.review_conversation(cid, generation["generation_id"], "AutoCorp", "accepted", "Quick podcast validation.")
        conversations.approve_conversation(cid, "AutoCorp")
        prep = conn.execute(
            "SELECT * FROM storytelling_preparations WHERE subject_type='ai_conversation' AND subject_id=? "
            "ORDER BY created_at DESC LIMIT 1",
            (cid,),
        ).fetchone()
        if prep is None:
            fail("Storytelling", "storytelling preparation was not created")
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Storytelling", exc)
    progress.phase_done("Storytelling")

    # Host Performance: no distinct verification exists in the original
    # implementation either - preserved as a pass-through checkpoint.
    progress.phase_start("Host Performance")
    progress.phase_done("Host Performance")

    # ----------------------------------------------------------------- #
    # Voice rendering (real Chatterbox synthesis)
    # ----------------------------------------------------------------- #
    progress.phase_start("Voice")
    progress.emit("Rendering conversation...")
    progress.emit("")
    stop_event = threading.Event()
    poller = threading.Thread(
        target=_poll_voice_render_progress,
        args=(db_path, cid, generation["generation_id"], progress, stop_event),
        daemon=True,
    )
    poller.start()
    try:
        render = ConversationVoiceService(conn, settings).render_conversation(cid, "quick-podcast-render")
        render_job_id = render["job"]["render_job_id"]
    except Exception as exc:
        detail = str(exc)
        try:
            rows = conn.execute(
                "SELECT turn_position, status, error_details, spoken_text "
                "FROM conversation_voice_turn_assets WHERE conversation_id=? "
                "ORDER BY turn_position",
                (cid,),
            ).fetchall()
            failed = [
                {
                    "turn_position": row["turn_position"],
                    "status": row["status"],
                    "error_details": row["error_details"],
                    "word_count": len(str(row["spoken_text"]).split()),
                    "text_sha256": hashlib.sha256(str(row["spoken_text"]).encode("utf-8")).hexdigest(),
                }
                for row in rows
                if row["status"] == "failed" or row["error_details"]
            ]
            if failed:
                detail = detail + " | failed_assets=" + json.dumps(failed, sort_keys=True)
        except Exception:
            pass
        fail("Voice", detail)
    finally:
        stop_event.set()
        poller.join(timeout=2)
    progress.emit("")
    progress.phase_done("Voice")

    # Artwork: no distinct verification exists in the original implementation
    # either - the actual artwork file is copied later, in the Package phase,
    # exactly as before. Preserved as a pass-through checkpoint.
    progress.phase_start("Artwork")
    progress.phase_done("Artwork")

    # ----------------------------------------------------------------- #
    # Assembly
    # ----------------------------------------------------------------- #
    progress.phase_start("Assembly")
    from clonecast.conversation_assembly_service import ConversationAssemblyService
    from clonecast.radio_episode_integration_service import RadioEpisodeIntegrationService

    try:
        progress.emit("Conversation Assembly...")
        assembly = ConversationAssemblyService(conn, settings).assemble_conversation(render_job_id, "quick-podcast-conversation-assembly")
        assembly_id = assembly["assembly"]["assembly_id"]
        progress.emit("PASS")
        progress.emit("")
    except Exception as exc:
        fail("Assembly", exc)

    try:
        progress.emit("Episode Assembly...")
        integration = RadioEpisodeIntegrationService(conn, settings)
        plan = integration.create_plan(episode_id, studio_id, f"{topic} - Quick Podcast", "quick-podcast-integration-plan")
        integration.add_component(plan["plan_id"], 0, "conversation", "quick-podcast-component", source_conversation_assembly_id=assembly_id)
        episode = integration.assemble_episode(plan["plan_id"], "quick-podcast-episode-assembly")
        job_id = episode["job"]["job_id"]
        progress.emit("PASS")
        progress.emit("")
    except Exception as exc:
        fail("Assembly", exc)
    progress.phase_done("Assembly")

    # ----------------------------------------------------------------- #
    # Audio QC
    # ----------------------------------------------------------------- #
    progress.phase_start("Audio QC")
    from clonecast.radio_episode_qc_service import RadioEpisodeQCService

    try:
        progress.emit("Running QC...")
        progress.emit("")
        qc = RadioEpisodeQCService(conn, settings)
        created = qc.create_qc_request(job_id, "quick-podcast-qc")
        qc_run = qc.run_qc(created["qc_request_id"])
        checks = qc_run["checks"]
        for check in checks:
            label = check.get("code", "check")
            status = "PASS" if check.get("status") == "passed" else check.get("status", "").upper()
            progress.emit(label)
            progress.emit(status)
            progress.emit("")
        blocking = [c for c in checks if c["severity"] == "blocking" and c["status"] == "failed"]
        if blocking:
            fail("Audio QC", "; ".join(c["code"] for c in blocking))
        qc.add_human_review(created["qc_request_id"], "AutoCorp", "approved", "Quick podcast QC validation.")
        readiness = qc.create_release_readiness(created["qc_request_id"])
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Audio QC", exc)
    progress.phase_done("Audio QC")

    # ----------------------------------------------------------------- #
    # Package
    # ----------------------------------------------------------------- #
    progress.phase_start("Package")
    from clonecast.radio_release_package_service import RadioReleasePackageService

    try:
        progress.emit("Creating Release Package...")
        package = RadioReleasePackageService(conn, settings).create_release_package(readiness["readiness_id"], "quick-podcast-package")
        package_dir = settings.project_root / package["package_path"]
        validation = RadioReleasePackageService(conn, settings).validate_release_package(package["package_id"])
        if not validation["valid"]:
            fail("Package", validation["problems"])
        progress.emit("PASS")
        progress.emit("")
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        fail("Package", exc)

    try:
        progress.emit("Copying output...")
        output_dir.mkdir(parents=True, exist_ok=True)
        shutil_copy2 = __import__("shutil").copy2
        shutil_copy2(package_dir / "episode.mp3", output_dir / "episode.mp3")
        shutil_copy2(package_dir / "artwork" / "youtube_thumbnail.png", output_dir / "thumbnail.png")
        shutil_copy2(research_path, output_dir / "research.md")
        turns = conn.execute(
            "SELECT position,spoken_text FROM ai_conversation_turns WHERE generation_id=? ORDER BY position",
            (generation["generation_id"],),
        ).fetchall()
        (output_dir / "script.md").write_text(
            "\n\n".join(f"## Turn {row['position'] + 1}\n{row['spoken_text']}" for row in turns),
            encoding="utf-8",
        )
        (output_dir / "qc_report.txt").write_text(
            json.dumps({"qc_request_id": created["qc_request_id"], "checks": [dict(c) for c in checks]}, indent=2),
            encoding="utf-8",
        )
        video = package_dir / "video" / "youtube.mp4"
        if video.is_file():
            shutil_copy2(video, output_dir / "episode.mp4")
        progress.emit("PASS")
        progress.emit("")

        progress.emit("Creating ZIP...")
        with zipfile.ZipFile(output_dir / "package.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for path in package_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(package_dir))
        progress.emit("PASS")
        progress.emit("")

        mp3 = output_dir / "episode.mp3"
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(mp3)],
            text=True, capture_output=True, timeout=30, check=True,
        )
        mp3_duration = float(json.loads(probe.stdout)["format"]["duration"])
        if mp3_duration < duration * 0.75:
            fail("Audio QC", f"final MP3 is materially under requested duration: {mp3_duration:.1f}s for requested {duration}s")

        result = {
            "ok": True,
            "output_dir": str(output_dir),
            "episode_id": episode_id,
            "package_id": package["package_id"],
            "qc_request_id": created["qc_request_id"],
            "mp3": str(mp3),
            "mp3_size": mp3.stat().st_size,
            "mp3_duration_seconds": mp3_duration,
            "mp3_sha256": _sha256(mp3),
            "storytelling_preparation_id": prep["preparation_id"],
            "publishing": "SKIPPED (TEST MODE)",
        }
    except QuickPodcastFailure:
        raise
    except Exception as exc:
        # Previously this final block had NO exception handler at all - any
        # failure here crashed with a raw traceback instead of the same
        # clean fail(phase, reason) contract every other phase uses. Fixed
        # for consistency/debuggability; the happy-path logic above is
        # unchanged.
        fail("Package", exc)
    finally:
        conn.close()

    progress.phase_done("Package")
    progress.phase_start("Publishing")
    progress.phase_done("Publishing", "SKIPPED (TEST MODE)")
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brains.quick_podcast_runner")
    p.add_argument("--repo", required=True)
    p.add_argument("--disp", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--research", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--show", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--duration-seconds", dest="duration_seconds", type=int, required=True)
    p.add_argument("--voice", default="")
    p.add_argument("--log-file", dest="log_file", required=True)
    p.add_argument("--phase-offset", dest="phase_offset", type=int, default=0,
                   help="how many [N/12] phases the parent process already completed "
                        "before invoking this module (default: 0, for standalone use)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = _run(args)
    except QuickPodcastFailure as exc:
        print(json.dumps({"ok": False, "phase": exc.phase, "reason": exc.reason}), flush=True)
        return 2
    except Exception as exc:  # last-resort: never let an unclassified crash hide the reason
        print(json.dumps({"ok": False, "phase": "CloneCast Health", "reason": f"{exc}\n{traceback.format_exc()}"}), flush=True)
        return 2
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

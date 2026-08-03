"""Guided CloneCast episode operator.

AutoCorp owns the terminal guidance, resumable state, and safety gates here.
CloneCast remains the authority for episode creation, generation, assembly,
validation, and any future publishing actions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import config


SESSION_DIRNAME = "guided_clonecast_episode_sessions"
PUBLISHING_LOCKED_REASON = "Owner review not completed"
PUBLISHING_ELIGIBLE_REASON = "Owner approved the completed episode"
PUBLISH_COMMANDS = {
    "publication-create",
    "publication-publish",
    "publication-process-due",
    "publication-resume",
    "publication-retry",
    "radio-publication-create",
}

_STAGE_ORDER = {
    "created": 0,
    "target_confirmed": 10,
    "clonecast_discovered": 20,
    "studio_selected": 30,
    "duration_selected": 40,
    "research_imported": 50,
    "research_accepted": 60,
    "approved_script_preserved": 70,
    "inputs_validated": 80,
    "saved_before_generation": 90,
    "episode_created": 100,
    "approved_script_imported": 110,
    "voice_assigned": 120,
    "speech_provider_preflight": 130,
    "speech_rendering": 140,
    "speech_rendered": 150,
    "speech_validated": 160,
    "episode_assembled": 170,
    "episode_validated": 180,
    "episode_mastered": 190,
    "listening_gate": 200,
    "review_rejected": 210,
    "section_regeneration_requested": 220,
    "owner_approved": 230,
}


class EpisodeBuildError(RuntimeError):
    """Raised for a truthful operator or CloneCast failure."""


@dataclass
class CloneCastResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    json_data: Any | None = None


@dataclass
class EpisodeSession:
    session_id: str
    clonecast_repo_path: str
    selected_studio_show: str | None = None
    requested_duration_seconds: int | None = None
    research_source: dict[str, Any] = field(default_factory=dict)
    research_import_status: str | None = None
    script_source: dict[str, Any] = field(default_factory=dict)
    script_checksum: str | None = None
    imported_script_checksum: str | None = None
    script_preserved_byte_for_byte: bool = False
    selected_host: str | None = None
    selected_voices: dict[str, str] = field(default_factory=dict)
    guests_or_callers: str | None = None
    media_mode: str | None = None
    clonecast_episode_identifiers: dict[str, str] = field(default_factory=dict)
    completed_stage: str = "created"
    failed_stage: str | None = None
    artifact_paths: dict[str, Any] = field(default_factory=dict)
    validation_evidence: dict[str, Any] = field(default_factory=dict)
    review_status: str = "not_started"
    owner_approval_status: str = "publishing_locked"
    publishing_lock_reason: str = PUBLISHING_LOCKED_REASON
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    clonecast_commands: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    available_next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "clonecast_repo_path": self.clonecast_repo_path,
            "selected_studio_show": self.selected_studio_show,
            "requested_duration_seconds": self.requested_duration_seconds,
            "research_source": self.research_source,
            "research_import_status": self.research_import_status,
            "script_source": self.script_source,
            "script_checksum": self.script_checksum,
            "imported_script_checksum": self.imported_script_checksum,
            "script_preserved_byte_for_byte": self.script_preserved_byte_for_byte,
            "selected_host": self.selected_host,
            "selected_voices": self.selected_voices,
            "guests_or_callers": self.guests_or_callers,
            "media_mode": self.media_mode,
            "clonecast_episode_identifiers": self.clonecast_episode_identifiers,
            "completed_stage": self.completed_stage,
            "failed_stage": self.failed_stage,
            "artifact_paths": self.artifact_paths,
            "validation_evidence": self.validation_evidence,
            "review_status": self.review_status,
            "owner_approval_status": self.owner_approval_status,
            "publishing_lock_reason": self.publishing_lock_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "clonecast_commands": self.clonecast_commands,
            "warnings": self.warnings,
            "available_next_actions": self.available_next_actions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EpisodeSession":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def session_dir() -> Path:
    return Path(config.DATA_DIR) / SESSION_DIRNAME


def session_path(session_id: str) -> Path:
    return session_dir() / f"{session_id}.json"


def managed_source_dir() -> Path:
    return session_dir() / "sources"


def save_session(session: EpisodeSession) -> Path:
    session.updated_at = _now()
    session_dir().mkdir(parents=True, exist_ok=True)
    path = session_path(session.session_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_session(session_id: str) -> EpisodeSession:
    path = session_path(session_id)
    if not path.is_file():
        raise EpisodeBuildError(f"session not found: {session_id}")
    return EpisodeSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checksum_file(path: Path) -> str:
    return checksum_bytes(path.read_bytes())


def _advance_stage(session: EpisodeSession, stage: str) -> None:
    current = _STAGE_ORDER.get(session.completed_stage, -1)
    requested = _STAGE_ORDER.get(stage, current)
    if requested >= current:
        session.completed_stage = stage


def _normalize_completed_stage_from_saved_state(session: EpisodeSession) -> None:
    if session.clonecast_episode_identifiers.get("research_id") and session.research_import_status == "accepted":
        _advance_stage(session, "research_accepted")


def normalize_studio_show(value: str) -> str:
    text = (value or "").strip()
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    return text


def _fail_session(session: EpisodeSession, stage: str, message: str) -> None:
    session.failed_stage = stage
    session.warnings.append(f"{stage}: {message}")
    save_session(session)


def clonecast_research_document(source: dict[str, Any], data: bytes) -> bytes:
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EpisodeBuildError("research must be valid UTF-8 for CloneCast ingestion") from exc
    title = "AutoCorp Guided Research"
    if source.get("source_type") == "uploaded_file" and source.get("path"):
        title = f"AutoCorp Guided Research: {Path(str(source['path'])).name}"
    payload = {
        "title": title,
        "body": body,
        "collected_at": _now(),
        "tags": ["autocorp-guided-episode"],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


def materialize_pasted_research(session: EpisodeSession, source: dict[str, Any], data: bytes) -> Path:
    managed_source_dir().mkdir(parents=True, exist_ok=True)
    target = managed_source_dir() / f"{session.session_id}-research.json"
    target.write_bytes(clonecast_research_document(source, data))
    return target


def parse_duration(value: str) -> int:
    text = (value or "").strip().lower()
    if not text:
        raise ValueError("duration is required")
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)?", text
    )
    if not match:
        raise ValueError("duration must look like 600s, 10m, or 1h")
    amount = float(match.group(1))
    unit = match.group(2) or "m"
    multiplier = 1 if unit.startswith("s") else 60 if unit.startswith("m") else 3600
    seconds = int(round(amount * multiplier))
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return seconds


def _read_uploaded_source(source: dict[str, Any], *, label: str) -> tuple[dict[str, Any], bytes]:
    raw_path = source.get("path")
    if not raw_path:
        raise EpisodeBuildError(f"{label} uploaded file path is missing")
    path = Path(str(raw_path)).expanduser().resolve()
    if not path.is_file():
        raise EpisodeBuildError(f"{label} file does not exist: {path}")
    data = path.read_bytes()
    if not data.strip():
        raise EpisodeBuildError(f"{label} file is empty: {path}")
    metadata = {
        "kind": "file",
        "source_type": "uploaded_file",
        "path": str(path),
        "sha256": checksum_bytes(data),
    }
    for key in ("upload_id", "original_filename", "size_bytes", "managed_sha256"):
        if key in source:
            metadata[key] = source[key]
    return metadata, data


def _read_pasted_source(source: dict[str, Any], *, label: str) -> tuple[dict[str, Any], bytes]:
    text = source.get("text")
    if not isinstance(text, str) or not text.strip():
        raise EpisodeBuildError(f"{label} text is required")
    data = text.encode("utf-8")
    checksum = checksum_bytes(data)
    return {
        "kind": "pasted_text",
        "source_type": "pasted_text",
        "text": text,
        "sha256": checksum,
        "length_bytes": len(data),
    }, data


def _raw_input_existing_path(raw: str) -> Path | None:
    path = Path(raw).expanduser()
    try:
        return path if path.exists() else None
    except OSError:
        return None


def read_source(value: Any, *, label: str) -> tuple[dict[str, Any], bytes]:
    if isinstance(value, dict):
        source_type = value.get("source_type")
        if source_type == "pasted_text":
            return _read_pasted_source(value, label=label)
        if source_type == "uploaded_file":
            return _read_uploaded_source(value, label=label)
        raise EpisodeBuildError(f"{label} input source type is missing or unsupported")

    raw = str(value or "")
    if raw.startswith("@"):
        path = Path(raw[1:]).expanduser().resolve()
    else:
        path = _raw_input_existing_path(raw)
    if raw.startswith("@") or path is not None:
        path = path.resolve()
        if not path.is_file():
            raise EpisodeBuildError(f"{label} file does not exist: {path}")
        data = path.read_bytes()
        if not data.strip():
            raise EpisodeBuildError(f"{label} file is empty: {path}")
        return {"kind": "file", "source_type": "uploaded_file", "path": str(path), "sha256": checksum_bytes(data)}, data
    data = raw.encode("utf-8")
    if not data.strip():
        raise EpisodeBuildError(f"{label} text is required")
    return {
        "kind": "pasted_text",
        "source_type": "pasted_text",
        "text": raw,
        "sha256": checksum_bytes(data),
        "length_bytes": len(data),
    }, data


class CloneCastCLI:
    def __init__(
        self, repo: Path, *, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None, timeout: int = 1800
    ):
        self.repo = repo.expanduser().resolve()
        self.runner = runner or subprocess.run
        self.timeout = timeout

    def validate_repo(self) -> None:
        if not self.repo.is_dir():
            raise EpisodeBuildError(f"CloneCast repository does not exist: {self.repo}")
        required = [self.repo / "src" / "clonecast" / "cli.py", self.repo / "migrations"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise EpisodeBuildError("invalid CloneCast repository; missing " + ", ".join(missing))

    def base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        allow_publish: bool = False,
        timeout: int | float | None = None,
    ) -> CloneCastResult:
        if any(arg in PUBLISH_COMMANDS for arg in args) and not allow_publish:
            raise EpisodeBuildError(
                "AutoCorp blocked a publishing command; publishing requires a separate explicit command"
            )
        command = [sys.executable, "-m", "clonecast.cli", *args]
        completed = self.runner(
            command,
            cwd=str(self.repo),
            env=self.base_env(),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=self.timeout if timeout is None else timeout,
        )
        parsed = None
        if completed.stdout.strip():
            try:
                parsed = json.loads(completed.stdout)
            except json.JSONDecodeError:
                parsed = None
        return CloneCastResult(command, completed.returncode, completed.stdout, completed.stderr, parsed)

    def checked(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
        timeout: int | float | None = None,
    ) -> CloneCastResult:
        result = self.run(args, input_text=input_text, timeout=timeout)
        if result.returncode != 0:
            raise EpisodeBuildError(
                "CloneCast command failed: " + " ".join(args) + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def checked_monitored(
        self,
        args: list[str],
        *,
        timeout: int,
        heartbeat_interval: int,
        heartbeat: Callable[[int], None],
    ) -> CloneCastResult:
        """Run a long CloneCast command with periodic liveness heartbeats.

        ``subprocess.run(..., timeout=...)`` is intentionally avoided here: it
        gives no chance to prove progress while Chatterbox is still rendering.
        A live child process is treated as healthy until the computed deadline
        expires or it exits with a real failure.
        """
        if not hasattr(self, "runner"):
            return self.checked(args)
        if self.runner is not subprocess.run:
            return self.checked(args, timeout=timeout)
        if any(arg in PUBLISH_COMMANDS for arg in args):
            raise EpisodeBuildError(
                "AutoCorp blocked a publishing command; publishing requires a separate explicit command"
            )

        command = [sys.executable, "-m", "clonecast.cli", *args]
        started = time.monotonic()
        deadline = started + timeout
        next_heartbeat = started
        proc = subprocess.Popen(
            command,
            cwd=str(self.repo),
            env=self.base_env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout = ""
        stderr = ""
        try:
            while True:
                now = time.monotonic()
                if now >= next_heartbeat and proc.poll() is None:
                    heartbeat(int(now - started))
                    next_heartbeat = now + max(1, heartbeat_interval)
                remaining = deadline - now
                if remaining <= 0:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    raise EpisodeBuildError(
                        "CloneCast command timed out during "
                        + " ".join(args)
                        + f" after {timeout}s. The worker process was stopped; verify GPU memory before retrying.\n"
                        + f"stdout:\n{stdout}\nstderr:\n{stderr}"
                    )
                wait_for = min(max(0.05, remaining), max(1, heartbeat_interval))
                try:
                    stdout, stderr = proc.communicate(timeout=wait_for)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except Exception:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            raise

        parsed = None
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        result = CloneCastResult(command, proc.returncode, stdout, stderr, parsed)
        if result.returncode != 0:
            raise EpisodeBuildError(
                "CloneCast command failed: " + " ".join(args) + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def discover_commands(self) -> set[str]:
        result = self.checked(["--help"])
        commands = set(re.findall(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b", result.stdout))
        return commands


def _record_command(session: EpisodeSession, result: CloneCastResult) -> None:
    session.clonecast_commands.append(
        {
            "command": result.command[3:],
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    )


def _assignment_for_speaker(payload: Any, speaker: str) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        raise EpisodeBuildError("CloneCast returned unexpected JSON for script-voice-list")
    for item in payload:
        if isinstance(item, dict) and item.get("speaker") == speaker:
            return item
    return None


def _ensure_voice_assigned(
    session: EpisodeSession,
    cli: CloneCastCLI,
    *,
    script_id: str,
    speaker: str,
    voice_profile_id: str,
) -> None:
    list_result = cli.checked(["script-voice-list", script_id])
    _record_command(session, list_result)
    existing = _assignment_for_speaker(_require_json(list_result, "script-voice-list"), speaker)
    if existing is not None:
        existing_voice = str(existing.get("voice_profile_id") or "")
        if existing_voice == voice_profile_id:
            session.validation_evidence["voice_assignment"] = {
                "status": "already_assigned",
                "speaker": speaker,
                "voice_profile_id": voice_profile_id,
                "assignment_id": existing.get("assignment_id"),
            }
            session.completed_stage = "voice_assigned"
            save_session(session)
            return
        raise EpisodeBuildError(
            "saved script already has a different voice assigned for "
            f"{speaker}: {existing_voice}; requested {voice_profile_id}. "
            "AutoCorp will not overwrite an existing CloneCast voice assignment silently."
        )

    assign_result = cli.checked(
        [
            "script-voice-assign",
            "--script-id",
            script_id,
            "--speaker",
            speaker,
            "--voice-profile-id",
            voice_profile_id,
        ]
    )
    _record_command(session, assign_result)
    session.validation_evidence["voice_assignment"] = {
        "status": "created",
        "speaker": speaker,
        "voice_profile_id": voice_profile_id,
    }
    session.completed_stage = "voice_assigned"
    save_session(session)


def _prompt(prompt: str, input_func: Callable[[str], Any]) -> Any:
    value = input_func(prompt)
    return value.strip() if isinstance(value, str) else value


def approve_for_publishing(session_id: str, *, owner: str = "owner") -> EpisodeSession:
    session = load_session(session_id)
    final_audio = session.artifact_paths.get("final_audio")
    if not final_audio:
        raise EpisodeBuildError("approval cannot occur before a completed artifact exists")
    if not Path(str(final_audio)).is_file():
        raise EpisodeBuildError(f"approval cannot occur because final audio is missing: {final_audio}")
    session.review_status = "approved"
    session.owner_approval_status = "publishing_eligible"
    session.publishing_lock_reason = PUBLISHING_ELIGIBLE_REASON
    session.validation_evidence["owner_approval"] = {
        "owner": owner,
        "approved_at": _now(),
        "final_audio": str(final_audio),
    }
    session.completed_stage = "owner_approved"
    save_session(session)
    return session


def reject_after_review(session_id: str, *, reason: str = "") -> EpisodeSession:
    session = load_session(session_id)
    session.review_status = "rejected"
    session.owner_approval_status = "publishing_locked"
    session.publishing_lock_reason = PUBLISHING_LOCKED_REASON
    if reason:
        session.warnings.append(f"Owner rejected completed episode: {reason}")
    session.completed_stage = "review_rejected"
    save_session(session)
    return session


def regenerate_section(session_id: str, section_id: str) -> EpisodeSession:
    session = load_session(session_id)
    approved = set(session.validation_evidence.get("approved_sections", []))
    if section_id in approved:
        raise EpisodeBuildError("selected section is already approved; AutoCorp will not rebuild it")
    failed = set(session.validation_evidence.get("failed_sections", []))
    rejected = set(session.validation_evidence.get("rejected_sections", []))
    if section_id not in failed and section_id not in rejected:
        raise EpisodeBuildError("only failed or rejected sections can be regenerated")
    session.validation_evidence.setdefault("regeneration_requests", []).append(
        {"section_id": section_id, "requested_at": _now()}
    )
    session.completed_stage = "section_regeneration_requested"
    save_session(session)
    return session


def _print_listening_gate(session: EpisodeSession, output: Callable[[str], None]) -> None:
    if session.artifact_paths.get("final_audio"):
        output("EPISODE READY FOR OWNER REVIEW")
        output("")
    output("")
    if session.owner_approval_status == "publishing_eligible":
        output("PUBLISHING ELIGIBLE")
        output(f"Reason: {PUBLISHING_ELIGIBLE_REASON}")
    else:
        output("PUBLISHING LOCKED")
        output(f"Reason: {PUBLISHING_LOCKED_REASON}")
    output("")
    output(f"Episode ID: {session.clonecast_episode_identifiers.get('episode_id', '(unknown)')}")
    output(f"Script ID: {session.clonecast_episode_identifiers.get('script_id', '(unknown)')}")
    output(f"Episode status: {session.completed_stage}")
    output(f"Requested duration: {session.requested_duration_seconds or '(unknown)'} seconds")
    output(f"Actual generated duration: {session.validation_evidence.get('actual_duration_seconds', '(unavailable)')}")
    output(f"Raw assembled audio path: {session.artifact_paths.get('raw_audio', '(not generated)')}")
    output(f"Mastered review audio path: {session.artifact_paths.get('final_audio', '(not generated)')}")
    output(f"File size: {session.validation_evidence.get('final_audio_file_size_bytes', '(unavailable)')}")
    output(f"Audio format: {session.validation_evidence.get('final_audio_format', '(unavailable)')}")
    output(f"Input LUFS: {session.validation_evidence.get('input_i_lufs', '(unavailable)')}")
    output(f"Output LUFS: {session.validation_evidence.get('output_i_lufs', '(unavailable)')}")
    output(f"Output true peak: {session.validation_evidence.get('output_tp_dbtp', '(unavailable)')}")
    output(f"Mastering status: {session.validation_evidence.get('mastering_status', '(unavailable)')}")
    output(f"Chapter or section audio paths: {json.dumps(session.artifact_paths.get('sections', []))}")
    output(f"Script preservation status: {session.script_preserved_byte_for_byte}")
    output(f"Source SHA-256: {session.script_checksum or '(unavailable)'}")
    output(f"Stored SHA-256: {session.imported_script_checksum or '(unavailable)'}")
    output(f"Validation results: {json.dumps(session.validation_evidence, sort_keys=True)}")
    output(f"Warnings: {json.dumps(session.warnings)}")
    output("Available next actions:")
    for action in session.available_next_actions:
        output(f"- {action}")


def _require_json(result: CloneCastResult, stage: str) -> Any:
    if result.json_data is None:
        raise EpisodeBuildError(f"CloneCast returned non-JSON output for {stage}: {result.stdout[:1000]}")
    return result.json_data


def _require_mapping(value: Any, stage: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EpisodeBuildError(f"CloneCast returned unexpected JSON for {stage}")
    return value


def _show_research(session: EpisodeSession, cli: CloneCastCLI, research_id: str) -> dict[str, Any]:
    result = cli.checked(["research-show", research_id])
    _record_command(session, result)
    return _require_mapping(_require_json(result, "research-show"), "research-show")


def _recover_research(session: EpisodeSession, cli: CloneCastCLI, research_id: str) -> dict[str, Any]:
    result = cli.checked(["research-recover", "--research-id", research_id])
    _record_command(session, result)
    payload = _require_json(result, "research-recover")
    if not isinstance(payload, list) or not payload:
        raise EpisodeBuildError("CloneCast did not return a research recovery outcome")
    first = payload[0]
    if not isinstance(first, dict):
        raise EpisodeBuildError("CloneCast returned unexpected JSON for research-recover")
    return first


def _canonical_research_id_from_import(outcome: dict[str, Any]) -> str:
    status = outcome.get("status")
    if status == "duplicate":
        duplicate_of = outcome.get("duplicate_of_research_id")
        if not duplicate_of:
            raise EpisodeBuildError("CloneCast reported duplicate research without duplicate_of_research_id")
        return str(duplicate_of)
    research_id = outcome.get("research_id")
    if not research_id:
        raise EpisodeBuildError("CloneCast did not return a research_id")
    return str(research_id)


def _ensure_research_accepted(
    session: EpisodeSession,
    cli: CloneCastCLI,
    output: Callable[[str], None],
    *,
    research_path: Path | None = None,
) -> str:
    stage = "research_acceptance"
    try:
        imported_id = session.clonecast_episode_identifiers.get("imported_research_id")
        accepted_id = session.clonecast_episode_identifiers.get("research_id")
        candidate_id = accepted_id or imported_id
        if not candidate_id:
            if research_path is None:
                saved_path = session.artifact_paths.get("managed_research")
                research_path = Path(str(saved_path)) if saved_path else None
            if (research_path is None or not research_path.is_file()) and session.research_source.get(
                "source_type"
            ) == "pasted_text":
                research_source, research_bytes = _read_pasted_source(session.research_source, label="research")
                research_path = materialize_pasted_research(session, research_source, research_bytes)
                session.research_source = research_source
                session.artifact_paths["managed_research"] = str(research_path)
                session.validation_evidence["research_source"] = {
                    "source_type": "pasted_text",
                    "sha256": research_source["sha256"],
                    "length_bytes": len(research_bytes),
                }
                save_session(session)
            if research_path is None or not research_path.is_file():
                raise EpisodeBuildError(
                    "resume cannot continue research acceptance because the managed research file is missing"
                )
            result = cli.checked(["research-ingest", str(research_path)])
            _record_command(session, result)
            payload = _require_json(result, "research-ingest")
            if not isinstance(payload, list) or not payload:
                raise EpisodeBuildError("CloneCast did not return a research ingestion outcome")
            outcome = payload[0]
            if not isinstance(outcome, dict):
                raise EpisodeBuildError("CloneCast returned unexpected JSON for research-ingest")
            session.research_import_status = str(outcome.get("status") or "")
            if outcome.get("research_id"):
                session.clonecast_episode_identifiers["imported_research_id"] = str(outcome["research_id"])
            candidate_id = _canonical_research_id_from_import(outcome)
            output("Research imported")
            session.completed_stage = "research_imported"
            save_session(session)

        record = _show_research(session, cli, candidate_id)
        output("Research validated")
        state = record.get("lifecycle_state") or record.get("status")
        if state == "duplicate":
            duplicate_of = record.get("duplicate_of_research_id")
            if not duplicate_of:
                raise EpisodeBuildError(f"research item is duplicate without canonical accepted item: {candidate_id}")
            candidate_id = str(duplicate_of)
            record = _show_research(session, cli, candidate_id)
            state = record.get("lifecycle_state") or record.get("status")
        if state != "accepted":
            recovery = _recover_research(session, cli, candidate_id)
            session.research_import_status = str(recovery.get("status") or state or "")
            if recovery.get("status") == "duplicate" and recovery.get("duplicate_of_research_id"):
                candidate_id = str(recovery["duplicate_of_research_id"])
            record = _show_research(session, cli, candidate_id)
            state = record.get("lifecycle_state") or record.get("status")
        if state != "accepted":
            raise EpisodeBuildError(
                f"research acceptance failed for {candidate_id}; final state is {state or 'unknown'}"
            )
        session.clonecast_episode_identifiers["research_id"] = candidate_id
        session.research_import_status = "accepted"
        session.validation_evidence["accepted_research"] = {
            "research_id": candidate_id,
            "lifecycle_state": state,
            "content_hash": record.get("content_hash"),
            "current_path": record.get("current_path"),
        }
        session.failed_stage = None
        session.completed_stage = "research_accepted"
        save_session(session)
        output("Research accepted")
        return candidate_id
    except EpisodeBuildError as exc:
        _fail_session(session, stage, str(exc))
        raise


def _find_output(outputs: list[dict[str, Any]], output_type: str) -> dict[str, Any]:
    for item in outputs:
        if item.get("output_type") == output_type:
            return item
    raise EpisodeBuildError(f"CloneCast did not report {output_type} output")


def _find_artifact(artifacts: list[dict[str, Any]], artifact_type: str) -> dict[str, Any]:
    for item in artifacts:
        if item.get("artifact_type") == artifact_type:
            return item
    raise EpisodeBuildError(f"CloneCast did not report {artifact_type} mastering artifact")


def _record_mastering_result(session: EpisodeSession, payload: dict[str, Any]) -> None:
    job = payload.get("job", {})
    mastering_job_id = job.get("mastering_job_id")
    if not mastering_job_id:
        raise EpisodeBuildError("CloneCast did not return an episode mastering job_id")
    session.clonecast_episode_identifiers["episode_mastering_job_id"] = mastering_job_id
    validation = payload.get("validation") or {}
    if not validation.get("valid"):
        raise EpisodeBuildError("CloneCast did not return passing mastering validation")
    source = _find_artifact(payload.get("artifacts", []), "source_mp3")
    mastered = _find_artifact(payload.get("artifacts", []), "mastered_mp3")
    raw_path = Path(str(source["path"]))
    final_path = Path(str(mastered["path"]))
    if not raw_path.is_file() or raw_path.stat().st_size <= 1024:
        raise EpisodeBuildError(f"raw audio artifact is missing or implausibly small: {raw_path}")
    if not final_path.is_file() or final_path.stat().st_size <= 1024:
        raise EpisodeBuildError(f"mastered audio artifact is missing or implausibly small: {final_path}")
    measurements = validation.get("measurements", {})
    session.artifact_paths["raw_audio"] = str(raw_path)
    session.artifact_paths["final_audio"] = str(final_path)
    session.validation_evidence["raw_audio_sha256"] = source.get("sha256")
    session.validation_evidence["raw_audio_file_size_bytes"] = source.get("file_size_bytes")
    session.validation_evidence["raw_audio_duration_seconds"] = source.get("duration_seconds")
    session.validation_evidence["mastered_audio_sha256"] = mastered.get("sha256")
    session.validation_evidence["final_audio_sha256"] = mastered.get("sha256")
    session.validation_evidence["final_audio_file_size_bytes"] = mastered.get("file_size_bytes")
    session.validation_evidence["actual_duration_seconds"] = mastered.get("duration_seconds")
    session.validation_evidence["final_audio_format"] = f"{mastered.get('container')}/{mastered.get('codec')}"
    session.validation_evidence["input_i_lufs"] = job.get("input_i_lufs")
    session.validation_evidence["input_tp_dbtp"] = job.get("input_tp_dbtp")
    session.validation_evidence["input_lra_lu"] = job.get("input_lra_lu")
    session.validation_evidence["output_i_lufs"] = measurements.get("output_i_lufs", job.get("output_i_lufs"))
    session.validation_evidence["output_tp_dbtp"] = measurements.get("output_tp_dbtp", job.get("output_tp_dbtp"))
    session.validation_evidence["output_lra_lu"] = measurements.get("output_lra_lu", job.get("output_lra_lu"))
    session.validation_evidence["mastering_status"] = job.get("status")
    session.validation_evidence["mastering_validation"] = validation
    session.validation_evidence["mastering_retry_count"] = int(
        session.validation_evidence.get("mastering_retry_count", 0)
    )


def _master_existing_episode(session: EpisodeSession, cli: CloneCastCLI) -> None:
    audio_job_id = session.clonecast_episode_identifiers.get("episode_audio_job_id")
    if not audio_job_id:
        raise EpisodeBuildError("resume cannot master because episode_audio_job_id is missing")
    result = cli.checked(
        [
            "episode-audio-master",
            "--episode-audio-job-id",
            audio_job_id,
            "--idempotency-key",
            f"{session.session_id}:episode-audio-master:{session.validation_evidence.get('mastering_retry_count', 0)}",
        ]
    )
    _record_command(session, result)
    _record_mastering_result(session, _require_json(result, "episode-audio-master"))
    session.completed_stage = "episode_mastered"
    save_session(session)


def _script_word_count(session: EpisodeSession) -> int:
    script_path = session.artifact_paths.get("managed_script")
    if not script_path:
        return 0
    path = Path(str(script_path))
    if not path.is_file():
        return 0
    try:
        return len(re.findall(r"\b[\w']+\b", path.read_text(encoding="utf-8")))
    except UnicodeDecodeError:
        return 0


def calculate_speech_render_timeout(session: EpisodeSession) -> int:
    target_seconds = int(session.requested_duration_seconds or 0)
    script_words = _script_word_count(session)
    sized = max(
        float(config.CLONECAST_SPEECH_TIMEOUT_MIN_SECONDS),
        target_seconds * float(config.CLONECAST_SPEECH_TIMEOUT_PER_TARGET_SECOND),
        script_words * float(config.CLONECAST_SPEECH_TIMEOUT_PER_SCRIPT_WORD),
    )
    return int(round(sized + float(config.CLONECAST_SPEECH_TIMEOUT_STARTUP_GRACE_SECONDS)))


def _record_speech_failure_cleanup(session: EpisodeSession, cli: CloneCastCLI) -> None:
    cleanup: dict[str, Any] = {
        "attempted": True,
        "checked_at": _now(),
        "worker_stop_requested": "delegated_to_clonecast_speech_render_failure_path",
        "vram_release_confirmed": False,
    }
    try:
        result = cli.checked(["speech-provider-check"])
        _record_command(session, result)
        payload = _require_json(result, "speech-provider-check")
        cleanup["provider_check"] = payload
        preflight = payload.get("preflight") if isinstance(payload, dict) else None
        if isinstance(preflight, dict):
            may_begin = preflight.get("may_begin")
            free_vram = preflight.get("free_vram_mib")
            cleanup["free_vram_mib"] = free_vram
            cleanup["vram_release_confirmed"] = may_begin is not False
    except Exception as exc:  # cleanup evidence must never hide the real render error
        cleanup["error"] = str(exc)
    session.validation_evidence["speech_failure_cleanup"] = cleanup
    save_session(session)


def _render_speech(
    session: EpisodeSession,
    cli: CloneCastCLI,
    *,
    script_id: str,
    output: Callable[[str], None],
) -> CloneCastResult:
    timeout = calculate_speech_render_timeout(session)
    heartbeat_interval = max(1, int(config.CLONECAST_SPEECH_HEARTBEAT_SECONDS))
    session.validation_evidence["speech_render_timeout"] = {
        "timeout_seconds": timeout,
        "target_duration_seconds": session.requested_duration_seconds,
        "script_word_count": _script_word_count(session),
        "heartbeat_interval_seconds": heartbeat_interval,
        "previous_fixed_timeout_seconds": getattr(cli, "timeout", 1800),
    }
    session.failed_stage = None
    session.completed_stage = "speech_rendering"
    save_session(session)
    output("Audio generation started")

    def heartbeat(elapsed_seconds: int) -> None:
        session.validation_evidence["speech_render_heartbeat"] = {
            "stage": "speech-render",
            "status": "running",
            "elapsed_seconds": elapsed_seconds,
            "updated_at": _now(),
        }
        save_session(session)
        output(f"Audio generation still running ({elapsed_seconds}s elapsed)")

    try:
        return cli.checked_monitored(
            [
                "speech-render",
                "--script-id",
                script_id,
                "--idempotency-key",
                f"{session.session_id}:speech-render",
            ],
            timeout=timeout,
            heartbeat_interval=heartbeat_interval,
            heartbeat=heartbeat,
        )
    except EpisodeBuildError as exc:
        _fail_session(session, "speech_render", str(exc))
        _record_speech_failure_cleanup(session, cli)
        raise


def run_guided_episode_build(
    repo: str,
    *,
    resume: str | None = None,
    input_func: Callable[[str], Any] = input,
    output: Callable[[str], None] = print,
    clonecast_factory: Callable[[Path], CloneCastCLI] | None = None,
) -> EpisodeSession:
    cli = (clonecast_factory or (lambda path: CloneCastCLI(path)))(Path(repo))
    cli.validate_repo()

    if resume:
        session = load_session(resume)
        if Path(session.clonecast_repo_path).resolve() != Path(repo).expanduser().resolve():
            raise EpisodeBuildError("resume session belongs to a different CloneCast repository")
    else:
        session = EpisodeSession(
            session_id=f"acce_{uuid.uuid4().hex}", clonecast_repo_path=str(Path(repo).expanduser().resolve())
        )
        save_session(session)

    confirmation = _prompt(f"Confirm CloneCast target {session.clonecast_repo_path} by typing YES: ", input_func)
    if confirmation != "YES":
        raise EpisodeBuildError("target project confirmation was not completed")
    _normalize_completed_stage_from_saved_state(session)
    _advance_stage(session, "target_confirmed")
    save_session(session)

    config_check = cli.checked(["config-check"])
    _record_command(session, config_check)
    commands = cli.discover_commands()
    session.validation_evidence["clonecast_compatible"] = True
    session.validation_evidence["discovered_commands"] = sorted(commands)
    _normalize_completed_stage_from_saved_state(session)
    _advance_stage(session, "clonecast_discovered")
    save_session(session)

    if (
        resume
        and session.clonecast_episode_identifiers.get("episode_audio_job_id")
        and session.validation_evidence.get("mastering_status") != "completed"
    ):
        if "episode-audio-master" not in commands:
            raise EpisodeBuildError("CloneCast compatibility blocker; missing supported command: episode-audio-master")
        _master_existing_episode(session, cli)
        session.review_status = "ready_for_owner_review"
        session.owner_approval_status = "publishing_locked"
        session.publishing_lock_reason = PUBLISHING_LOCKED_REASON
        session.available_next_actions = [
            "Listen to full episode",
            "Review individual sections",
            "Regenerate a selected failed or rejected section",
            "Save and continue later",
            "Approve for publishing",
            "Reject and keep publishing locked",
        ]
        session.completed_stage = "listening_gate"
        save_session(session)
        _print_listening_gate(session, output)
        action = _prompt("Approve, reject, regenerate a section, or save for later? ", input_func).lower()
        if action.startswith("approve"):
            return approve_for_publishing(session.session_id, owner="owner")
        if action.startswith("reject"):
            return reject_after_review(session.session_id, reason="owner rejected at listening gate")
        if action.startswith("regenerate"):
            section = _prompt("Which failed or rejected section ID should be regenerated? ", input_func)
            return regenerate_section(session.session_id, section)
        return session

    if "research-show" not in commands or "research-recover" not in commands:
        raise EpisodeBuildError(
            "CloneCast compatibility blocker; missing supported commands: research-show, research-recover"
        )

    if "radio-studio-list" in commands:
        studios = cli.checked(["radio-studio-list"])
        _record_command(session, studios)
        if studios.json_data:
            output("Available CloneCast studios/shows:")
            for item in studios.json_data:
                output(f"- {item.get('studio_id')}: {item.get('display_name', item.get('stable_name', 'Untitled'))}")
    if not session.selected_studio_show:
        session.selected_studio_show = normalize_studio_show(
            _prompt("Which CloneCast show/studio should be used? ", input_func)
        )
    else:
        session.selected_studio_show = normalize_studio_show(session.selected_studio_show)
    if not session.selected_studio_show:
        raise EpisodeBuildError("studio/show is required")
    _normalize_completed_stage_from_saved_state(session)
    _advance_stage(session, "studio_selected")
    save_session(session)

    if session.requested_duration_seconds is None:
        session.requested_duration_seconds = parse_duration(_prompt("How long should the podcast be? ", input_func))
    _normalize_completed_stage_from_saved_state(session)
    _advance_stage(session, "duration_selected")
    save_session(session)

    temp_research = None
    if not session.clonecast_episode_identifiers.get("research_id") or session.failed_stage == "research_acceptance":
        if not session.research_source:
            has_research = _prompt("Do you have research? [yes/no] ", input_func).lower()
            if has_research not in {"yes", "y"}:
                raise EpisodeBuildError("research is required before generation can start")
            research_source, research_bytes = read_source(
                _prompt("Provide research by file path, @file, or pasted text: ", input_func), label="research"
            )
            session.research_source = research_source
            if research_source.get("source_type") == "pasted_text":
                temp_research = materialize_pasted_research(session, research_source, research_bytes)
            else:
                temp_research = Path(str(research_source["path"]))
            session.artifact_paths["managed_research"] = str(temp_research)
            session.validation_evidence["research_source"] = {
                "source_type": research_source.get("source_type"),
                "sha256": research_source.get("sha256"),
                "length_bytes": len(research_bytes),
            }
            save_session(session)
        _ensure_research_accepted(session, cli, output, research_path=temp_research)

    temp_script = managed_source_dir() / f"{session.session_id}-approved-script.txt"
    if not session.script_preserved_byte_for_byte:
        has_script = _prompt("Do you have a final approved script? [yes/no] ", input_func).lower()
        if has_script not in {"yes", "y"}:
            raise EpisodeBuildError("a final approved script is required for this guided operator")
        script_source, script_bytes = read_source(
            _prompt("Provide approved script by file path, @file, or pasted text: ", input_func),
            label="approved script",
        )
        session.script_source = script_source
        session.script_checksum = checksum_bytes(script_bytes)
        managed_source_dir().mkdir(parents=True, exist_ok=True)
        temp_script.write_bytes(script_bytes)
        session.artifact_paths["managed_script"] = str(temp_script)
        session.imported_script_checksum = checksum_file(temp_script)
        session.script_preserved_byte_for_byte = session.script_checksum == session.imported_script_checksum
        if not session.script_preserved_byte_for_byte:
            raise EpisodeBuildError("approved script checksum changed during AutoCorp import")
        session.completed_stage = "approved_script_preserved"
        save_session(session)
    elif not temp_script.is_file() and session.artifact_paths.get("managed_script"):
        temp_script = Path(str(session.artifact_paths["managed_script"]))

    if not session.selected_host:
        session.selected_host = _prompt("Which host should be used? ", input_func)
    voice = session.selected_voices.get("host") or _prompt("Which host voice/profile should be used? ", input_func)
    if not session.selected_host or not voice:
        raise EpisodeBuildError("host and voice are required")
    if voice == session.selected_host:
        raise EpisodeBuildError("host display name cannot be used as a voice profile ID")
    session.selected_voices = {"host": voice}
    if session.guests_or_callers is None:
        session.guests_or_callers = _prompt("Are guests or callers required? Describe them or say no: ", input_func)
    media = session.media_mode or _prompt("Audio-only or video? [audio/video] ", input_func).lower()
    if media not in {"audio", "audio-only", "video"}:
        raise EpisodeBuildError("media choice must be audio or video")
    session.media_mode = "video" if media == "video" else "audio-only"
    session.completed_stage = "inputs_validated"
    save_session(session)

    missing = []
    for command in (
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
    ):
        if command not in commands:
            missing.append(command)
    if missing:
        raise EpisodeBuildError("CloneCast compatibility blocker; missing supported commands: " + ", ".join(missing))

    start = _prompt("Start generation now? [yes/no] ", input_func).lower()
    if start not in {"yes", "y"}:
        session.completed_stage = "saved_before_generation"
        save_session(session)
        return session

    research_id = session.clonecast_episode_identifiers.get("research_id")
    if not research_id:
        raise EpisodeBuildError("accepted research_id was not returned by CloneCast")
    _ensure_research_accepted(session, cli, output)
    episode_id = session.clonecast_episode_identifiers.get("episode_id")
    if not episode_id:
        episode_result = cli.checked(
            [
                "episode-create",
                "--research-id",
                session.clonecast_episode_identifiers["research_id"],
                "--title",
                f"{session.selected_studio_show} Guided Episode",
                "--idempotency-key",
                f"{session.session_id}:episode-create",
            ]
        )
        _record_command(session, episode_result)
        episode_payload = _require_json(episode_result, "episode-create")
        episode_id = episode_payload.get("episode_id")
        if not episode_id:
            raise EpisodeBuildError("CloneCast did not return an episode_id")
        session.clonecast_episode_identifiers["episode_id"] = episode_id
        session.completed_stage = "episode_created"
        save_session(session)

    script_id = session.clonecast_episode_identifiers.get("script_id")
    if script_id:
        import_payload = {
            "episode_id": episode_id,
            "script_id": script_id,
            "source_sha256": session.script_checksum,
            "stored_sha256": session.imported_script_checksum,
            "preserved_byte_for_byte": session.script_preserved_byte_for_byte,
            "voice_ready": True,
        }
    else:
        import_result = cli.checked(
            [
                "episode-script-import-approved",
                "--episode-id",
                episode_id,
                "--script-file",
                str(temp_script),
                "--idempotency-key",
                f"{session.session_id}:approved-script-import",
                "--default-speaker",
                "Host",
            ]
        )
        _record_command(session, import_result)
        import_payload = _require_mapping(
            _require_json(import_result, "episode-script-import-approved"), "episode-script-import-approved"
        )
        if not import_payload.get("preserved_byte_for_byte"):
            raise EpisodeBuildError("CloneCast did not prove approved script byte preservation")
        if not import_payload.get("voice_ready"):
            raise EpisodeBuildError("CloneCast did not report the imported script as voice-ready")
        session.clonecast_episode_identifiers["script_id"] = import_payload["script_id"]
        session.script_checksum = import_payload["source_sha256"]
        session.imported_script_checksum = import_payload["stored_sha256"]
        session.script_preserved_byte_for_byte = import_payload["source_sha256"] == import_payload["stored_sha256"]
        if not session.script_preserved_byte_for_byte:
            raise EpisodeBuildError("CloneCast source and stored approved script checksums differ")
        session.validation_evidence["script_import"] = import_payload
        session.completed_stage = "approved_script_imported"
        save_session(session)
        output("Approved script imported")

    _ensure_voice_assigned(
        session,
        cli,
        script_id=import_payload["script_id"],
        speaker="Host",
        voice_profile_id=voice,
    )
    output("Voice assigned")

    provider_check = cli.checked(["speech-provider-check"])
    _record_command(session, provider_check)
    provider_payload = _require_json(provider_check, "speech-provider-check")
    session.validation_evidence["speech_provider_preflight"] = provider_payload
    preflight = provider_payload.get("preflight") if isinstance(provider_payload, dict) else None
    if isinstance(preflight, dict) and preflight.get("may_begin") is False:
        raise EpisodeBuildError("CloneCast speech provider preflight reported generation may not begin")
    session.completed_stage = "speech_provider_preflight"
    save_session(session)

    speech_result = _render_speech(session, cli, script_id=import_payload["script_id"], output=output)
    _record_command(session, speech_result)
    speech_payload = _require_json(speech_result, "speech-render")
    speech_job = speech_payload.get("job", {})
    speech_job_id = speech_job.get("job_id")
    if not speech_job_id:
        raise EpisodeBuildError("CloneCast did not return a speech render job_id")
    session.clonecast_episode_identifiers["speech_job_id"] = speech_job_id
    session.artifact_paths["sections"] = [
        item.get("output_path") for item in speech_payload.get("segments", []) if item.get("output_path")
    ]
    session.completed_stage = "speech_rendered"
    save_session(session)

    speech_validation = cli.checked(["speech-render-validate", speech_job_id])
    _record_command(session, speech_validation)
    session.validation_evidence["speech_render"] = _require_json(speech_validation, "speech-render-validate")
    session.completed_stage = "speech_validated"
    save_session(session)

    audio_result = cli.checked(
        [
            "episode-audio-assemble",
            "--script-id",
            import_payload["script_id"],
            "--speech-job-id",
            speech_job_id,
            "--idempotency-key",
            f"{session.session_id}:episode-audio-assemble",
        ]
    )
    _record_command(session, audio_result)
    audio_payload = _require_json(audio_result, "episode-audio-assemble")
    audio_job = audio_payload.get("job", {})
    audio_job_id = audio_job.get("job_id")
    if not audio_job_id:
        raise EpisodeBuildError("CloneCast did not return an episode audio job_id")
    session.clonecast_episode_identifiers["episode_audio_job_id"] = audio_job_id
    mp3 = _find_output(audio_payload.get("outputs", []), "mp3")
    raw_audio = Path(str(mp3["path"]))
    if not raw_audio.is_file() or raw_audio.stat().st_size <= 1024:
        raise EpisodeBuildError(f"raw audio artifact is missing or implausibly small: {raw_audio}")
    session.artifact_paths["raw_audio"] = str(raw_audio)
    session.validation_evidence["raw_audio_file_size_bytes"] = mp3.get("file_size_bytes")
    session.validation_evidence["raw_audio_format"] = f"{mp3.get('container')}/{mp3.get('codec')}"
    session.validation_evidence["raw_audio_sha256"] = mp3.get("sha256")
    session.validation_evidence["raw_audio_duration_seconds"] = mp3.get("duration_seconds")
    session.completed_stage = "episode_assembled"
    save_session(session)

    audio_validation = cli.checked(["episode-audio-validate", audio_job_id])
    _record_command(session, audio_validation)
    session.validation_evidence["episode_audio"] = _require_json(audio_validation, "episode-audio-validate")
    session.completed_stage = "episode_validated"
    save_session(session)

    _master_existing_episode(session, cli)

    review = _prompt("Review the generated episode now? [yes/no] ", input_func).lower()
    session.review_status = "ready_for_owner_review" if review in {"yes", "y"} else "saved_for_later_review"
    session.owner_approval_status = "publishing_locked"
    session.publishing_lock_reason = PUBLISHING_LOCKED_REASON
    session.available_next_actions = [
        "Listen to full episode",
        "Review individual sections",
        "Regenerate a selected failed or rejected section",
        "Save and continue later",
        "Approve for publishing",
        "Reject and keep publishing locked",
    ]
    session.completed_stage = "listening_gate"
    save_session(session)
    _print_listening_gate(session, output)
    action = _prompt("Approve, reject, regenerate a section, or save for later? ", input_func).lower()
    if action.startswith("approve"):
        return approve_for_publishing(session.session_id, owner="owner")
    if action.startswith("reject"):
        return reject_after_review(session.session_id, reason="owner rejected at listening gate")
    if action.startswith("regenerate"):
        section = _prompt("Which failed or rejected section ID should be regenerated? ", input_func)
        return regenerate_section(session.session_id, section)
    return session

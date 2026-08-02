"""Durable storage for AutoCorp Chat App sessions.

An AppSession is a thin, UI-facing record: chat transcript, upload provenance,
and the pending-question state. It is *not* a second copy of episode state —
studio/duration/research/script/voice/audio/publishing state lives in the
already-repaired ``guided_clonecast_episode.EpisodeSession`` (see
``brains/guided_clonecast_episode.py``), referenced here by
``episode_session_id`` and read live whenever the workflow summary is shown.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from brains import guided_clonecast_episode as episode


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChatMessage:
    role: str  # "assistant" | "user" | "system"
    kind: str  # "text" | "question" | "progress" | "error" | "file_card" | "listening_gate"
    text: str
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    input_kind: str | None = None  # "buttons" | "text" | "file" | "confirm" | None
    options: list[dict[str, str]] = field(default_factory=list)
    event: str | None = None
    technical_detail: str | None = None
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "kind": self.kind,
            "text": self.text,
            "input_kind": self.input_kind,
            "options": self.options,
            "event": self.event,
            "technical_detail": self.technical_detail,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatMessage":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: data[k] for k in allowed if k in data})


@dataclass
class UploadRecord:
    upload_id: str
    kind: str  # "research" | "script"
    original_filename: str
    managed_path: str
    original_sha256: str
    managed_sha256: str
    size_bytes: int
    uploaded_at: str = field(default_factory=_now)
    consumed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UploadRecord":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: data[k] for k in allowed if k in data})


@dataclass
class AppSession:
    session_id: str
    clonecast_repo_path: str
    title: str = "New episode"
    episode_session_id: str | None = None
    status: str = "new"  # new | running | awaiting_input | completed | failed
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    messages: list[ChatMessage] = field(default_factory=list)
    pending_question: dict[str, Any] | None = None
    uploads: list[UploadRecord] = field(default_factory=list)
    prefill: dict[str, str] = field(default_factory=dict)
    voice_advanced_unlocked: bool = False
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "clonecast_repo_path": self.clonecast_repo_path,
            "title": self.title,
            "episode_session_id": self.episode_session_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [m.to_dict() for m in self.messages],
            "pending_question": self.pending_question,
            "uploads": [u.to_dict() for u in self.uploads],
            "prefill": self.prefill,
            "voice_advanced_unlocked": self.voice_advanced_unlocked,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppSession":
        allowed = cls.__dataclass_fields__.keys()
        kwargs = {k: data[k] for k in allowed if k in data}
        kwargs["messages"] = [ChatMessage.from_dict(m) for m in data.get("messages", [])]
        kwargs["uploads"] = [UploadRecord.from_dict(u) for u in data.get("uploads", [])]
        return cls(**kwargs)

    def add_message(self, msg: ChatMessage) -> None:
        self.messages.append(msg)
        self.updated_at = _now()


def sessions_dir() -> Path:
    return Path(config.app_sessions_dir())


def session_path(session_id: str) -> Path:
    _validate_session_id(session_id)
    return sessions_dir() / f"{session_id}.json"


def _validate_session_id(session_id: str) -> None:
    if not session_id or not all(c.isalnum() or c in "-_" for c in session_id):
        raise ValueError(f"invalid session id: {session_id!r}")


def new_session_id() -> str:
    return f"appsess_{uuid.uuid4().hex}"


def save_session(session: AppSession) -> Path:
    session.updated_at = _now()
    sessions_dir().mkdir(parents=True, exist_ok=True)
    path = session_path(session.session_id)
    tmp = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
    tmp.write_text(json.dumps(session.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_session(session_id: str) -> AppSession:
    path = session_path(session_id)
    if not path.is_file():
        raise FileNotFoundError(f"session not found: {session_id}")
    return AppSession.from_dict(json.loads(path.read_text(encoding="utf-8")))


def session_exists(session_id: str) -> bool:
    try:
        return session_path(session_id).is_file()
    except ValueError:
        return False


def list_sessions() -> list[AppSession]:
    directory = sessions_dir()
    if not directory.is_dir():
        return []
    sessions = []
    for path in directory.glob("appsess_*.json"):
        try:
            sessions.append(AppSession.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError):
            continue
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions


def delete_failed_session(session_id: str) -> dict[str, Any]:
    """Delete one failed app session and AutoCorp-owned disposable files.

    This intentionally never mutates CloneCast production data. Cleanup is
    limited to AutoCorp session JSON and files under AutoCorp-managed runtime
    directories, and only for sessions whose current UI status is ``failed``.
    """
    app = load_session(session_id)
    if app.status != "failed":
        raise ValueError(f"session {session_id} is {app.status!r}, not failed")

    deleted_files: list[str] = []
    deleted_dirs: list[str] = []
    skipped_files: list[str] = []
    protected_app_paths = _app_paths_referenced_by_other_sessions(app)

    for upload in app.uploads:
        if _resolved_string(Path(upload.managed_path)) in protected_app_paths:
            skipped_files.append(upload.managed_path)
            continue
        _delete_owned_file(Path(upload.managed_path), deleted_files, skipped_files)

    upload_dir = _managed_upload_dir_for_session(app.session_id)
    if upload_dir is not None and upload_dir.is_dir():
        if _contains_protected_path(upload_dir, protected_app_paths):
            skipped_files.append(str(upload_dir))
        else:
            try:
                shutil.rmtree(upload_dir)
                deleted_dirs.append(str(upload_dir))
            except OSError:
                skipped_files.append(str(upload_dir))

    if app.episode_session_id:
        try:
            ep = episode.load_session(app.episode_session_id)
        except episode.EpisodeBuildError:
            ep = None
        if ep is not None:
            protected = _paths_referenced_by_other_sessions(app, ep)
            for key in ("managed_research", "managed_script"):
                path = ep.artifact_paths.get(key)
                if not path:
                    continue
                candidate = Path(str(path))
                resolved = _resolved_string(candidate)
                if resolved is None:
                    skipped_files.append(str(candidate))
                    continue
                if resolved in protected:
                    skipped_files.append(str(candidate))
                    continue
                _delete_owned_source_file(candidate, deleted_files, skipped_files)
            if _episode_referenced_by_other_app_session(app, ep.session_id):
                skipped_files.append(str(episode.session_path(ep.session_id)))
            else:
                _delete_episode_record(ep.session_id, deleted_files, skipped_files)

    path = session_path(app.session_id)
    try:
        path.unlink()
        deleted_files.append(str(path))
    except FileNotFoundError:
        pass

    return {
        "session_id": app.session_id,
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "skipped_files": skipped_files,
    }


def delete_all_failed_sessions() -> dict[str, Any]:
    results = [delete_failed_session(app.session_id) for app in list_sessions() if app.status == "failed"]
    return {
        "deleted_count": len(results),
        "deleted_session_ids": [item["session_id"] for item in results],
        "results": results,
    }


def _managed_upload_dir_for_session(session_id: str) -> Path | None:
    try:
        base = Path(config.app_uploads_dir()).resolve()
        candidate = (base / session_id).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate == base or base not in candidate.parents:
        return None
    return candidate


def _delete_owned_file(path: Path, deleted: list[str], skipped: list[str]) -> None:
    try:
        base = Path(config.app_uploads_dir()).resolve()
        candidate = path.resolve()
    except (OSError, RuntimeError):
        skipped.append(str(path))
        return
    if candidate == base or base not in candidate.parents:
        skipped.append(str(path))
        return
    try:
        candidate.unlink()
        deleted.append(str(candidate))
    except FileNotFoundError:
        pass
    except OSError:
        skipped.append(str(candidate))


def _delete_owned_source_file(path: Path, deleted: list[str], skipped: list[str]) -> None:
    try:
        base = episode.managed_source_dir().resolve()
        candidate = path.resolve()
    except (OSError, RuntimeError):
        skipped.append(str(path))
        return
    if candidate == base or base not in candidate.parents:
        skipped.append(str(path))
        return
    try:
        candidate.unlink()
        deleted.append(str(candidate))
    except FileNotFoundError:
        pass
    except OSError:
        skipped.append(str(candidate))


def _delete_episode_record(session_id: str, deleted: list[str], skipped: list[str]) -> None:
    path = episode.session_path(session_id)
    try:
        base = episode.session_dir().resolve()
        candidate = path.resolve()
    except (OSError, RuntimeError):
        skipped.append(str(path))
        return
    if candidate.parent != base:
        skipped.append(str(path))
        return
    try:
        candidate.unlink()
        deleted.append(str(candidate))
    except FileNotFoundError:
        pass
    except OSError:
        skipped.append(str(candidate))


def _app_paths_referenced_by_other_sessions(app: AppSession) -> set[str]:
    protected: set[str] = set()
    for other in list_sessions():
        if other.session_id == app.session_id:
            continue
        for upload in other.uploads:
            _add_resolved(protected, upload.managed_path)
    return protected


def _contains_protected_path(directory: Path, protected: set[str]) -> bool:
    try:
        resolved_dir = directory.resolve()
    except (OSError, RuntimeError):
        return True
    for item in protected:
        try:
            protected_path = Path(item).resolve()
        except (OSError, RuntimeError):
            continue
        if protected_path == resolved_dir or resolved_dir in protected_path.parents:
            return True
    return False


def _resolved_string(path: Path) -> str | None:
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return None


def _paths_referenced_by_other_sessions(app: AppSession, ep: episode.EpisodeSession) -> set[str]:
    protected: set[str] = set()
    for other in list_sessions():
        if other.session_id == app.session_id:
            continue
        for upload in other.uploads:
            _add_resolved(protected, upload.managed_path)
        if not other.episode_session_id:
            continue
        if other.episode_session_id == ep.session_id:
            for value in ep.artifact_paths.values():
                _add_artifact_paths(protected, value)
            continue
        try:
            other_ep = episode.load_session(other.episode_session_id)
        except episode.EpisodeBuildError:
            continue
        for value in other_ep.artifact_paths.values():
            _add_artifact_paths(protected, value)
    return protected


def _episode_referenced_by_other_app_session(app: AppSession, episode_session_id: str) -> bool:
    return any(
        other.session_id != app.session_id and other.episode_session_id == episode_session_id
        for other in list_sessions()
    )


def _add_artifact_paths(target: set[str], value: Any) -> None:
    if isinstance(value, str):
        _add_resolved(target, value)
    elif isinstance(value, list):
        for item in value:
            _add_artifact_paths(target, item)
    elif isinstance(value, dict):
        for item in value.values():
            _add_artifact_paths(target, item)


def _add_resolved(target: set[str], value: str) -> None:
    try:
        target.add(str(Path(value).resolve()))
    except (OSError, RuntimeError):
        pass

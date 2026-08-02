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
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


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

"""Bridges the browser chat UI to the repaired guided CloneCast episode
operator (``brains.guided_clonecast_episode.run_guided_episode_build``).

Design: the operator function is synchronous and drives itself entirely
through an ``input_func(prompt) -> str`` callback and an ``output(line)``
callback. This module runs that exact function, unmodified, on a background
thread per chat session, and implements ``input_func``/``output`` so that:

- Known internal-detail prompts (confirming the CloneCast target, "review the
  generated episode now?") are auto-answered without bothering the user.
- Prompts that need a user decision are turned into a friendly question
  (buttons where the choice set is known, free text otherwise), persisted to
  the AppSession, and the worker thread blocks on a queue until the browser
  posts an answer.
- Every ``output()`` call is translated into a real, backend-verified
  progress event — never a guessed or fabricated one.

This is the *only* place that drives the guided workflow from the web layer;
the workflow rules themselves are not duplicated here.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
from app import clonecast_client as cc
from app import file_service
from app import gpu_guard
from app import progress_events
from app import session_store as store
from brains import guided_clonecast_episode as episode

_CANCELLED = object()


class ChatControllerError(RuntimeError):
    pass


@dataclass
class EngineHandle:
    app_session_id: str
    thread: threading.Thread | None = None
    answer_queue: "queue.Queue[Any]" = field(default_factory=queue.Queue)
    updated_event: threading.Event = field(default_factory=threading.Event)
    pending_values: dict[str, Any] = field(default_factory=dict)
    studios: list[cc.Studio] = field(default_factory=list)
    voices: list[cc.VoiceProfile] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    known_episode_ids_before: frozenset[str] = frozenset()


_REGISTRY_LOCK = threading.Lock()
_HANDLES: dict[str, EngineHandle] = {}


def _get_handle(app_session_id: str) -> EngineHandle | None:
    with _REGISTRY_LOCK:
        return _HANDLES.get(app_session_id)


def _register(handle: EngineHandle) -> None:
    with _REGISTRY_LOCK:
        _HANDLES[handle.app_session_id] = handle


def _unregister(app_session_id: str) -> None:
    with _REGISTRY_LOCK:
        _HANDLES.pop(app_session_id, None)


PROMPT_PREFIXES: list[tuple[str, str]] = [
    ("Confirm CloneCast target", "confirm_target"),
    ("Which CloneCast show/studio should be used?", "studio"),
    ("How long should the podcast be?", "duration"),
    ("Do you have research?", "research_has"),
    ("Provide research by file path", "research_provide"),
    ("Do you have a final approved script?", "script_has"),
    ("Provide approved script by file path", "script_provide"),
    ("Which host should be used?", "host"),
    ("Which host voice/profile should be used?", "voice"),
    ("Are guests or callers required?", "guests"),
    ("Audio-only or video?", "media"),
    ("Start generation now?", "start_generation"),
    ("Review the generated episode now?", "review"),
    ("Approve, reject, regenerate a section, or save for later?", "listening_gate_action"),
    ("Which failed or rejected section ID should be regenerated?", "regenerate_section_id"),
]


def classify_prompt(prompt: str) -> str:
    for prefix, kind in PROMPT_PREFIXES:
        if prompt.startswith(prefix):
            return kind
    return "freeform"


_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s\b|minutes?|mins?|m\b|hours?|hrs?|h\b)", re.IGNORECASE)


def extract_prefill(message: str, studios: list[cc.Studio]) -> dict[str, str]:
    """Best-effort, deterministic keyword extraction from free text — never
    an LLM guess, and never used for safety-critical fields (host, voice)."""
    prefill: dict[str, str] = {}
    lower = (message or "").casefold()
    for studio in studios:
        if studio.display_name and studio.display_name.casefold() in lower:
            prefill["studio_id"] = studio.studio_id
            prefill["studio_display_name"] = studio.display_name
            break
    duration_match = _DURATION_RE.search(lower)
    if duration_match:
        candidate = f"{duration_match.group(1)}{duration_match.group(2)[0]}"
        try:
            episode.parse_duration(candidate)
            prefill["duration"] = candidate
        except ValueError:
            pass
    if re.search(r"\bno guests?\b|\bno callers?\b|\bsolo host\b", lower):
        prefill["guests"] = "no"
    if re.search(r"\baudio\s*-?\s*only\b", lower) or (" audio" in lower and "video" not in lower):
        prefill["media"] = "audio"
    elif "video" in lower:
        prefill["media"] = "video"
    return prefill


def _log_progress(app: store.AppSession, event: str, text: str) -> None:
    app.add_message(store.ChatMessage(role="assistant", kind="progress", text=text, event=event))


def _default_repo_path(repo_path: str | None) -> str:
    return repo_path or config.CLONECAST_REPO_PATH


def start_session(
    repo_path: str | None,
    first_message: str,
    *,
    clonecast_cli_factory: Any | None = None,
) -> store.AppSession:
    """``clonecast_cli_factory``, if given, is a ``Callable[[Path], CloneCastCLI]``
    used for both the friendly-label lookups and the guided workflow itself.
    It exists only so tests can inject a fake CloneCast CLI; production
    callers (the HTTP routes) never pass it, so the real CLI is always used."""
    repo_path = _default_repo_path(repo_path)
    session_id = store.new_session_id()
    app = store.AppSession(session_id=session_id, clonecast_repo_path=repo_path, status="new")
    if first_message:
        app.add_message(store.ChatMessage(role="user", kind="text", text=first_message))
    try:
        cli = clonecast_cli_factory(Path(repo_path)) if clonecast_cli_factory else None
        client = cc.CloneCastClient(repo_path, cli=cli)
        studios = client.list_studios()
    except episode.EpisodeBuildError as exc:
        app.status = "failed"
        app.error = {
            "type": "clonecast_unavailable",
            "step": "startup",
            "safe_message": (
                "CloneCast is not available at the configured path. Open Settings to choose the correct folder."
            ),
            "technical_message": str(exc),
            "retry_safe": True,
        }
        app.add_message(
            store.ChatMessage(
                role="assistant",
                kind="error",
                text=app.error["safe_message"],
                technical_detail=str(exc),
            )
        )
        store.save_session(app)
        return app
    app.prefill = extract_prefill(first_message, studios)
    if "studio_display_name" in app.prefill:
        app.title = app.prefill["studio_display_name"] + " episode"
        app.add_message(
            store.ChatMessage(role="assistant", kind="text", text=f"I found {app.prefill.pop('studio_display_name')}.")
        )
    app.status = "running"
    store.save_session(app)

    handle = EngineHandle(
        app_session_id=session_id,
        studios=studios,
        known_episode_ids_before=frozenset(p.stem for p in episode.session_dir().glob("acce_*.json")),
    )
    try:
        handle.voices = client.list_voices()
    except episode.EpisodeBuildError:
        handle.voices = []
    _register(handle)
    thread = threading.Thread(target=_worker, args=(session_id, repo_path, None, clonecast_cli_factory), daemon=True)
    handle.thread = thread
    thread.start()
    _wait_for_update(handle, session_id, timeout=25)
    return store.load_session(session_id)


def resume_session(app_session_id: str, *, clonecast_cli_factory: Any | None = None) -> store.AppSession:
    app = store.load_session(app_session_id)
    if app.status not in {"failed", "awaiting_input"}:
        raise ChatControllerError(f"session cannot be resumed from status {app.status!r}")
    if not app.episode_session_id:
        raise ChatControllerError("session has no linked episode to resume")
    repo_path = app.clonecast_repo_path
    cli = clonecast_cli_factory(Path(repo_path)) if clonecast_cli_factory else None
    client = cc.CloneCastClient(repo_path, cli=cli)
    handle = EngineHandle(app_session_id=app_session_id)
    try:
        handle.studios = client.list_studios()
        handle.voices = client.list_voices()
    except episode.EpisodeBuildError:
        pass
    if _recover_saved_pasted_research(app):
        app = store.load_session(app_session_id)
    app.status = "running"
    app.error = None
    app.add_message(store.ChatMessage(role="system", kind="text", text="Resuming from the last completed step."))
    store.save_session(app)
    _register(handle)
    thread = threading.Thread(
        target=_worker,
        args=(app_session_id, repo_path, app.episode_session_id, clonecast_cli_factory),
        daemon=True,
    )
    handle.thread = thread
    thread.start()
    _wait_for_update(handle, app_session_id, timeout=25)
    return store.load_session(app_session_id)


def _wait_for_update(handle: EngineHandle, app_session_id: str, timeout: float) -> None:
    """Block until the worker reaches a stable state (no longer "running") or
    the timeout elapses. A single ``updated_event`` pulse only means one
    output/question tick happened — several can fire back-to-back (e.g.
    "Research imported" / "validated" / "accepted"), so waking on the first
    one and returning immediately would race the worker and silently drop
    the rest of that burst from the caller's view of the session."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        fired = handle.updated_event.wait(timeout=remaining)
        handle.updated_event.clear()
        if not fired:
            return
        if store.load_session(app_session_id).status != "running":
            return


def get_state(app_session_id: str) -> store.AppSession:
    return store.load_session(app_session_id)


def list_sessions() -> list[store.AppSession]:
    return store.list_sessions()


def cancel_session(app_session_id: str) -> store.AppSession:
    handle = _get_handle(app_session_id)
    app = store.load_session(app_session_id)
    if handle is not None:
        handle.answer_queue.put(_CANCELLED)
        _wait_for_update(handle, app_session_id, timeout=10)
        app = store.load_session(app_session_id)
    else:
        app.status = "failed"
        app.error = {
            "type": "cancelled",
            "step": app.pending_question.get("field", "unknown") if app.pending_question else "unknown",
            "safe_message": "Operation cancelled.",
            "technical_message": "Cancelled by user; no active worker was found.",
            "retry_safe": True,
        }
        app.pending_question = None
        app.add_message(store.ChatMessage(role="system", kind="text", text="Cancelled."))
        store.save_session(app)
    return app


def register_upload(app_session_id: str, kind: str, filename: str, data: bytes) -> store.UploadRecord:
    app = store.load_session(app_session_id)
    saved = file_service.save_upload(app_session_id, kind, filename, data)
    record = store.UploadRecord(
        upload_id=f"upl_{saved.sha256[:16]}",
        kind=kind,
        original_filename=saved.original_filename,
        managed_path=saved.managed_path,
        original_sha256=saved.sha256,
        managed_sha256=saved.sha256,
        size_bytes=saved.size_bytes,
    )
    app.uploads.append(record)
    app.add_message(
        store.ChatMessage(
            role="user",
            kind="file_card",
            text=f"Uploaded {record.original_filename} ({record.size_bytes} bytes)",
            technical_detail=f"sha256: {record.managed_sha256}",
        )
    )
    store.save_session(app)
    return record


def submit_answer(app_session_id: str, payload: dict[str, Any]) -> store.AppSession:
    app = store.load_session(app_session_id)
    if app.status != "awaiting_input" or not app.pending_question:
        raise ChatControllerError("no pending question to answer")
    handle = _get_handle(app_session_id)
    if handle is None:
        raise ChatControllerError("this session has no active worker; use resume")

    field = app.pending_question.get("field")
    app.pending_question = None
    display_text, queue_value = _interpret_answer(app, handle, field, payload)
    if display_text is not None:
        app.add_message(store.ChatMessage(role="user", kind="text", text=display_text))
    app.status = "running" if queue_value is not None else "awaiting_input"
    store.save_session(app)
    if queue_value is not None:
        handle.answer_queue.put(queue_value)
        _wait_for_update(handle, app_session_id, timeout=25)
    return store.load_session(app_session_id)


def _interpret_answer(
    app: store.AppSession, handle: EngineHandle, field: str, payload: dict[str, Any]
) -> tuple[str | None, str | None]:
    value = payload.get("value")
    text = payload.get("text")
    upload_id = payload.get("upload_id")

    if field in {"research", "script"}:
        kind = field
        if upload_id:
            record = next((u for u in app.uploads if u.upload_id == upload_id and u.kind == kind), None)
            if not record:
                raise ChatControllerError("uploaded file not found for this session")
            record.consumed = True
            handle.pending_values[f"{kind}_value"] = {
                "source_type": "uploaded_file",
                "upload_id": record.upload_id,
                "path": record.managed_path,
                "original_filename": record.original_filename,
                "managed_sha256": record.managed_sha256,
                "size_bytes": record.size_bytes,
            }
            return f"Added {record.original_filename}", "yes"
        if text:
            handle.pending_values[f"{kind}_value"] = {"source_type": "pasted_text", "text": str(text)}
            return "(pasted text provided)", "yes"
        raise ChatControllerError("provide a file upload_id or pasted text")

    if field == "voice":
        voice_id = value or text
        if voice_id == "__cancel_draft__":
            app.pending_question = _build_question("voice", "", app, handle)
            return "Choose a different voice", None
        voice = next((v for v in handle.voices if v.voice_profile_id == voice_id), None)
        if voice is None:
            raise ChatControllerError("select a known voice profile")
        if voice.lifecycle_status != "approved" and not app.voice_advanced_unlocked:
            raise ChatControllerError("draft voices require the advanced override before selection")
        if voice.lifecycle_status != "approved" and payload.get("confirm_draft") is not True:
            # First click on a draft voice: require an explicit second confirmation.
            app.pending_question = _draft_voice_confirm_question(voice)
            return f"Selected {voice.label} (needs confirmation)", None
        return voice.label, voice.voice_profile_id

    if field == "studio":
        studio_id = value or text
        studio = next((s for s in handle.studios if s.studio_id == studio_id), None)
        if studio is None:
            raise ChatControllerError("select a known studio")
        return studio.display_name, studio.studio_id

    if field == "listening_gate_action" and value == "reject":
        reason = text or "No reason given"
        app.add_message(store.ChatMessage(role="system", kind="text", text=f"Rejection reason: {reason}"))
        return "Reject", "reject"

    if value is not None:
        return str(value), str(value)
    if text is not None:
        return str(text), str(text)
    raise ChatControllerError("answer must include value, text, or upload_id")


def _draft_voice_confirm_question(voice: cc.VoiceProfile) -> dict[str, Any]:
    return {
        "field": "voice",
        "text": (
            f"{voice.display_name} ({voice.lifecycle_status}) has not been approved for production use. "
            "Use it anyway for this episode?"
        ),
        "input_kind": "buttons",
        "options": [
            {"label": "Use this draft voice anyway", "value": voice.voice_profile_id, "confirm_draft": True},
            {"label": "Choose a different voice", "value": "__cancel_draft__"},
        ],
    }


def unlock_voice_advanced(app_session_id: str) -> store.AppSession:
    app = store.load_session(app_session_id)
    app.voice_advanced_unlocked = True
    store.save_session(app)
    return app


def _recover_saved_pasted_research(app: store.AppSession) -> bool:
    if not app.episode_session_id:
        return False
    try:
        ep = episode.load_session(app.episode_session_id)
    except episode.EpisodeBuildError:
        return False
    if ep.research_source or ep.clonecast_episode_identifiers.get("research_id"):
        return False
    text = _extract_failed_pasted_research(app)
    if text is None:
        return False
    data = text.encode("utf-8")
    ep.research_source = {
        "kind": "pasted_text",
        "source_type": "pasted_text",
        "text": text,
        "sha256": episode.checksum_bytes(data),
        "length_bytes": len(data),
        "recovered_from": "saved_app_session_error_detail",
    }
    ep.failed_stage = "research_acceptance"
    ep.validation_evidence["research_source"] = {
        "source_type": "pasted_text",
        "sha256": ep.research_source["sha256"],
        "length_bytes": len(data),
        "recovered_from": "saved_app_session_error_detail",
    }
    episode.save_session(ep)
    app.add_message(
        store.ChatMessage(
            role="system",
            kind="text",
            text="Recovered the originally pasted research from saved session data.",
        )
    )
    store.save_session(app)
    return True


def _extract_failed_pasted_research(app: store.AppSession) -> str | None:
    for msg in reversed(app.messages):
        detail = msg.technical_detail or ""
        match = re.search(r"File name too long: '(.+)'\Z", detail, re.DOTALL)
        if match:
            return match.group(1)
    for index, msg in enumerate(app.messages):
        if (
            msg.role == "assistant"
            and msg.kind == "question"
            and msg.input_kind == "file_or_text"
            and "research" in msg.text.lower()
        ):
            for candidate in app.messages[index + 1 :]:
                if candidate.role == "user" and candidate.kind == "text" and candidate.text != "(pasted text provided)":
                    return candidate.text
                if candidate.role == "assistant" and candidate.kind == "question":
                    break
    return None


# --------------------------------------------------------------------------- #
# Worker thread
# --------------------------------------------------------------------------- #


def _worker(
    app_session_id: str,
    repo_path: str,
    resume_episode_id: str | None,
    clonecast_cli_factory: Any | None = None,
) -> None:
    handle = _get_handle(app_session_id)
    assert handle is not None

    def input_func(prompt: str) -> Any:
        return _resolve_input(app_session_id, handle, prompt)

    def output_func(line: str) -> None:
        _handle_output(app_session_id, handle, line)

    try:
        session = episode.run_guided_episode_build(
            repo_path,
            resume=resume_episode_id,
            input_func=input_func,
            output=output_func,
            clonecast_factory=clonecast_cli_factory,
        )
        _on_worker_success(app_session_id, session)
    except episode.EpisodeBuildError as exc:
        _on_worker_error(app_session_id, str(exc), technical=str(exc), retry_safe=True)
    except _CancelledSignal:
        _on_worker_error(
            app_session_id,
            "Operation cancelled.",
            technical="Cancelled by user via the answer queue.",
            retry_safe=True,
        )
    except Exception as exc:  # error boundary: never lose the session on an unhandled exception
        _on_worker_error(
            app_session_id,
            "AutoCorp hit an unexpected internal error.",
            technical=f"{type(exc).__name__}: {exc}",
            retry_safe=True,
        )
    finally:
        handle.updated_event.set()
        _unregister(app_session_id)


class _CancelledSignal(RuntimeError):
    pass


def _resolve_input(app_session_id: str, handle: EngineHandle, prompt: str) -> Any:
    kind = classify_prompt(prompt)
    app = store.load_session(app_session_id)

    if kind == "confirm_target":
        if not app.episode_session_id:
            # The operator has already created and saved its EpisodeSession by
            # this point (see run_guided_episode_build), so the linked id can
            # be discovered now rather than only after the whole run succeeds
            # - which matters because a run that fails partway through must
            # still be resumable.
            new_ids = {p.stem for p in episode.session_dir().glob("acce_*.json")} - handle.known_episode_ids_before
            if len(new_ids) == 1:
                app.episode_session_id = next(iter(new_ids))
        _log_progress(app, "clonecast_target_confirmed", "CloneCast target confirmed.")
        store.save_session(app)
        return "YES"

    if kind == "research_provide":
        return handle.pending_values.pop("research_value")

    if kind == "script_provide":
        return handle.pending_values.pop("script_value")

    if kind == "review":
        return "yes"

    if kind == "research_has":
        unconsumed = next((u for u in app.uploads if u.kind == "research" and not u.consumed), None)
        if unconsumed:
            unconsumed.consumed = True
            handle.pending_values["research_value"] = {
                "source_type": "uploaded_file",
                "upload_id": unconsumed.upload_id,
                "path": unconsumed.managed_path,
                "original_filename": unconsumed.original_filename,
                "managed_sha256": unconsumed.managed_sha256,
                "size_bytes": unconsumed.size_bytes,
            }
            store.save_session(app)
            return "yes"

    if kind == "script_has":
        unconsumed = next((u for u in app.uploads if u.kind == "script" and not u.consumed), None)
        if unconsumed:
            unconsumed.consumed = True
            handle.pending_values["script_value"] = {
                "source_type": "uploaded_file",
                "upload_id": unconsumed.upload_id,
                "path": unconsumed.managed_path,
                "original_filename": unconsumed.original_filename,
                "managed_sha256": unconsumed.managed_sha256,
                "size_bytes": unconsumed.size_bytes,
            }
            store.save_session(app)
            return "yes"

    if kind == "studio" and app.prefill.get("studio_id"):
        studio_id = app.prefill.pop("studio_id")
        studio = next((s for s in handle.studios if s.studio_id == studio_id), None)
        store.save_session(app)
        if studio:
            _log_progress(app, "show_selected", f"Show selected: {studio.display_name}")
            store.save_session(app)
            return studio_id

    if kind == "duration" and app.prefill.get("duration"):
        duration = app.prefill.pop("duration")
        store.save_session(app)
        _log_progress(app, "duration_saved", f"Duration saved: {duration}")
        store.save_session(app)
        return duration

    if kind == "guests" and app.prefill.get("guests"):
        value = app.prefill.pop("guests")
        store.save_session(app)
        return value

    if kind == "media" and app.prefill.get("media"):
        value = app.prefill.pop("media")
        store.save_session(app)
        return value

    question = _build_question(kind, prompt, app, handle)
    app.pending_question = question
    app.status = "awaiting_input"
    app.add_message(
        store.ChatMessage(
            role="assistant",
            kind="question",
            text=question["text"],
            input_kind=question["input_kind"],
            options=question.get("options", []),
        )
    )
    store.save_session(app)
    handle.updated_event.set()
    answer = handle.answer_queue.get()
    if answer is _CANCELLED:
        raise _CancelledSignal()
    if kind == "start_generation" and str(answer).strip().lower() in {"yes", "y"}:
        _run_gpu_guard(app_session_id, handle)
    return answer


def _build_question(kind: str, prompt: str, app: store.AppSession, handle: EngineHandle) -> dict[str, Any]:
    if kind == "studio":
        return {
            "field": "studio",
            "text": "Which show should this episode be for?",
            "input_kind": "buttons",
            "options": [{"label": s.display_name, "value": s.studio_id} for s in handle.studios],
        }
    if kind == "duration":
        return {
            "field": "duration",
            "text": "How long should the episode be?",
            "input_kind": "buttons_or_text",
            "options": [{"label": v, "value": v} for v in ("15 minutes", "30 minutes", "45 minutes", "60 minutes")],
        }
    if kind in {"research_has", "research_provide"}:
        return {
            "field": "research",
            "text": "Add your research (PDF or TXT) or paste research text. Research is required before generation.",
            "input_kind": "file_or_text",
        }
    if kind in {"script_has", "script_provide"}:
        return {
            "field": "script",
            "text": "Now add the final approved script (TXT). It is preserved exactly, byte for byte.",
            "input_kind": "file_or_text",
        }
    if kind == "host":
        return {"field": "host", "text": "Which host should be used?", "input_kind": "text"}
    if kind == "voice":
        approved = [v for v in handle.voices if v.lifecycle_status == "approved"]
        pool = handle.voices if app.voice_advanced_unlocked else approved
        labels = cc.disambiguate_voice_labels(pool)
        options = [{"label": labels[v.voice_profile_id], "value": v.voice_profile_id} for v in pool]
        return {
            "field": "voice",
            "text": "Which approved voice should this host use?",
            "input_kind": "buttons",
            "options": options,
            "advanced_available": not app.voice_advanced_unlocked and len(pool) < len(handle.voices),
        }
    if kind == "guests":
        return {
            "field": "guests",
            "text": "Are any guests or callers needed?",
            "input_kind": "buttons_or_text",
            "options": [{"label": "No guests or callers", "value": "no"}],
        }
    if kind == "media":
        return {
            "field": "media",
            "text": "Audio only, or video?",
            "input_kind": "buttons",
            "options": [{"label": "Audio only", "value": "audio"}, {"label": "Video", "value": "video"}],
        }
    if kind == "start_generation":
        return {
            "field": "start_generation",
            "text": "Everything is set. Start generation now?",
            "input_kind": "buttons",
            "options": [
                {"label": "Start Generation", "value": "yes"},
                {"label": "Not yet — save for later", "value": "no"},
            ],
        }
    if kind == "listening_gate_action":
        return {
            "field": "listening_gate_action",
            "text": "The episode is ready to listen. What would you like to do?",
            "input_kind": "buttons",
            "options": [
                {"label": "Approve (still does not publish)", "value": "approve"},
                {"label": "Reject", "value": "reject"},
                {"label": "Regenerate a section", "value": "regenerate"},
                {"label": "Save for later", "value": "save"},
            ],
        }
    if kind == "regenerate_section_id":
        return {
            "field": "regenerate_section_id",
            "text": "Which section ID should be regenerated?",
            "input_kind": "text",
        }
    return {"field": "freeform", "text": prompt, "input_kind": "text"}


def _handle_output(app_session_id: str, handle: EngineHandle, line: str) -> None:
    app = store.load_session(app_session_id)
    if line.strip() == progress_events.LISTENING_GATE_MARKER:
        _append_listening_gate_message(app)
        store.save_session(app)
        handle.updated_event.set()
        return
    event, text = progress_events.classify_output_line(line)
    if event:
        app.add_message(store.ChatMessage(role="assistant", kind="progress", text=text, event=event))
        store.save_session(app)
        handle.updated_event.set()


def _run_gpu_guard(app_session_id: str, handle: EngineHandle) -> None:
    """Real, honest GPU headroom check before the Chatterbox audio stage.
    Never fakes a pass: verifies actual free VRAM, unloads Ollama through its
    own API/CLI only if that VRAM is actually needed, and stops the workflow
    (preserving the session) rather than silently proceeding or falling back
    to another device if headroom still cannot be verified afterward."""
    if not config.GPU_GUARD_ENABLED:
        return

    def emit(event: str, text: str) -> None:
        app = store.load_session(app_session_id)
        _log_progress(app, event, text)
        store.save_session(app)
        handle.updated_event.set()

    reservation = gpu_guard.reserve_for_stage(
        "Chatterbox audio generation",
        required_mb=config.CHATTERBOX_REQUIRED_VRAM_MB,
        gpu_name_substring=config.CHATTERBOX_GPU_NAME_SUBSTRING,
        output=emit,
        max_wait_seconds=config.GPU_GUARD_MAX_WAIT_SECONDS,
    )
    if not reservation.ok:
        raise episode.EpisodeBuildError(
            "Audio generation stopped because the GPU ran out of memory. "
            "No publishing occurred. You can safely resume or regenerate. "
            f"Detail: {reservation.failure_reason}"
        )


def _append_listening_gate_message(app: store.AppSession) -> None:
    if not app.episode_session_id:
        return
    try:
        ep = episode.load_session(app.episode_session_id)
    except episode.EpisodeBuildError:
        return
    if config.GPU_GUARD_ENABLED:
        gpu_guard.release_stage(
            "Chatterbox audio generation",
            gpu_name_substring=config.CHATTERBOX_GPU_NAME_SUBSTRING,
            output=lambda event, text: _log_progress(app, event, text),
        )
    final_audio = ep.artifact_paths.get("final_audio")
    text = (
        f"Episode ready for review.\n"
        f"Duration: {ep.validation_evidence.get('actual_duration_seconds', 'unknown')} seconds\n"
        f"File size: {ep.validation_evidence.get('final_audio_file_size_bytes', 'unknown')} bytes\n"
        f"Checksum: {ep.validation_evidence.get('final_audio_sha256', 'unknown')}\n"
        f"Publishing locked: {ep.owner_approval_status != 'publishing_eligible'}"
    )
    app.add_message(
        store.ChatMessage(
            role="assistant",
            kind="listening_gate",
            text=text,
            technical_detail=str(final_audio),
        )
    )


def _on_worker_success(app_session_id: str, ep_session: episode.EpisodeSession) -> None:
    app = store.load_session(app_session_id)
    app.episode_session_id = ep_session.session_id
    if ep_session.owner_approval_status == "publishing_eligible":
        app.status = "completed"
        app.add_message(
            store.ChatMessage(role="assistant", kind="text", text="Approved. Publishing remains locked in Phase 1.")
        )
    elif ep_session.review_status == "rejected":
        app.status = "completed"
        app.add_message(store.ChatMessage(role="assistant", kind="text", text="Rejected. Publishing remains locked."))
    elif ep_session.completed_stage == "saved_before_generation":
        app.status = "completed"
        app.add_message(
            store.ChatMessage(
                role="assistant", kind="text", text="Saved. You can resume any time before generation starts."
            )
        )
    else:
        app.status = "completed"
        app.add_message(store.ChatMessage(role="assistant", kind="text", text="Saved."))
    app.pending_question = None
    store.save_session(app)


def workflow_summary(app: store.AppSession) -> dict[str, Any]:
    ep: episode.EpisodeSession | None = None
    if app.episode_session_id:
        try:
            ep = episode.load_session(app.episode_session_id)
        except episode.EpisodeBuildError:
            ep = None
    if ep is None:
        return {
            "project": Path(app.clonecast_repo_path).name,
            "show": None,
            "target_duration_seconds": None,
            "research_file": None,
            "research_status": "not started",
            "approved_script": None,
            "script_checksum_status": "not started",
            "host": None,
            "voice": None,
            "guests_or_callers": None,
            "media_mode": None,
            "current_step": app.status,
            "last_completed_step": None,
            "output_path": None,
            "listening_gate_status": "not reached",
            "publishing_lock_status": "locked",
        }
    research_source = ep.research_source or {}
    script_source = ep.script_source or {}
    return {
        "project": Path(app.clonecast_repo_path).name,
        "show": ep.selected_studio_show,
        "target_duration_seconds": ep.requested_duration_seconds,
        "research_file": research_source.get("path") or ("pasted text" if research_source else None),
        "research_status": ep.research_import_status or "not started",
        "approved_script": script_source.get("path") or ("pasted text" if script_source else None),
        "script_checksum_status": "verified" if ep.script_preserved_byte_for_byte else "not verified",
        "host": ep.selected_host,
        "voice": ep.selected_voices.get("host"),
        "guests_or_callers": ep.guests_or_callers,
        "media_mode": ep.media_mode,
        "current_step": ep.completed_stage,
        "last_completed_step": ep.completed_stage,
        "failed_step": ep.failed_stage,
        "output_path": ep.artifact_paths.get("final_audio"),
        "listening_gate_status": "ready to listen" if ep.artifact_paths.get("final_audio") else "not reached",
        "publishing_lock_status": "eligible" if ep.owner_approval_status == "publishing_eligible" else "locked",
    }


def _on_worker_error(app_session_id: str, safe_message: str, *, technical: str, retry_safe: bool) -> None:
    app = store.load_session(app_session_id)
    app.status = "failed"
    app.pending_question = None
    app.error = {
        "type": "episode_build_error",
        "step": app.messages[-1].event if app.messages and app.messages[-1].event else "unknown",
        "safe_message": safe_message,
        "technical_message": technical,
        "retry_safe": retry_safe,
    }
    app.add_message(
        store.ChatMessage(
            role="assistant",
            kind="error",
            text=f"{safe_message} Your session was saved. Press Resume after the problem is fixed.",
            technical_detail=technical,
        )
    )
    store.save_session(app)

"""HTTP routes for the AutoCorp Chat App.

Kept deliberately thin: every route delegates to ``app.chat_controller``,
``app.file_service``, or ``app.system_status``. No workflow rules live here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

import config
from app import chat_controller as controller
from app import file_service
from app import session_store as store
from app import system_status
from app.schemas import AnswerRequest, SessionCreateRequest
from brains import guided_clonecast_episode as episode

router = APIRouter()
_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

UPLOAD_KINDS = {"research", "script"}


def _session_summary(app: store.AppSession) -> dict:
    return {
        "session_id": app.session_id,
        "title": app.title,
        "status": app.status,
        "created_at": app.created_at,
        "updated_at": app.updated_at,
        "clonecast_repo_path": app.clonecast_repo_path,
        "episode_session_id": app.episode_session_id,
    }


def _session_detail(app: store.AppSession) -> dict:
    return {
        **_session_summary(app),
        "messages": [m.to_dict() for m in app.messages],
        "pending_question": app.pending_question,
        "uploads": [u.to_dict() for u in app.uploads],
        "voice_advanced_unlocked": app.voice_advanced_unlocked,
        "error": app.error,
        "workflow_summary": controller.workflow_summary(app),
        "has_active_worker": controller.has_active_worker(app.session_id),
    }


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "server_ready": True}


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {"local_app_address": f"http://{config.APP_HOST}:{config.APP_PORT}"},
    )


@router.get("/api/status")
def api_status() -> dict:
    active = sum(1 for s in store.list_sessions() if s.status in {"running", "awaiting_input"})
    return system_status.build_status_report(active_sessions=active)


@router.get("/api/lifecycle/active")
def api_lifecycle_active() -> dict:
    sessions = [
        _session_summary(s)
        for s in controller.list_sessions()
        if s.status == "running"
    ]
    return {"active": bool(sessions), "sessions": sessions}


@router.post("/api/lifecycle/cancel-active")
def api_lifecycle_cancel_active() -> dict:
    results = []
    for app in controller.list_sessions():
        if app.status != "running":
            continue
        try:
            cancelled = controller.cancel_session(app.session_id)
            results.append({"session_id": app.session_id, "status": cancelled.status, "ok": True})
        except Exception as exc:
            results.append({"session_id": app.session_id, "status": "error", "ok": False, "error": str(exc)})
    return {"cancelled": results, "ok": all(item["ok"] for item in results)}


@router.get("/api/sessions")
def api_list_sessions() -> list[dict]:
    return [_session_summary(s) for s in controller.list_sessions()]


@router.post("/api/sessions")
def api_create_session(body: SessionCreateRequest) -> dict:
    app = controller.start_session(body.repo_path, body.message)
    return _session_detail(app)


@router.delete("/api/sessions/failed")
def api_delete_failed_sessions() -> dict:
    return store.delete_all_failed_sessions()


def _get_session_or_404(session_id: str) -> store.AppSession:
    if not store.session_exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")
    return store.load_session(session_id)


@router.delete("/api/sessions/{session_id}")
def api_delete_session(session_id: str) -> dict:
    _get_session_or_404(session_id)
    try:
        return store.delete_failed_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/sessions/{session_id}")
def api_get_session(session_id: str) -> dict:
    app = _get_session_or_404(session_id)
    return _session_detail(app)


@router.post("/api/sessions/{session_id}/resume")
def api_resume_session(session_id: str) -> dict:
    _get_session_or_404(session_id)
    try:
        app = controller.resume_session(session_id)
    except controller.ChatControllerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_detail(app)


@router.post("/api/sessions/{session_id}/answer")
def api_answer_session(session_id: str, body: AnswerRequest) -> dict:
    _get_session_or_404(session_id)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        app = controller.submit_answer(session_id, payload)
    except controller.ChatControllerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _session_detail(app)


@router.post("/api/sessions/{session_id}/cancel")
def api_cancel_session(session_id: str) -> dict:
    _get_session_or_404(session_id)
    app = controller.cancel_session(session_id)
    return _session_detail(app)


@router.post("/api/sessions/{session_id}/voice-advanced")
def api_voice_advanced(session_id: str) -> dict:
    _get_session_or_404(session_id)
    app = controller.unlock_voice_advanced(session_id)
    return _session_detail(app)


@router.post("/api/sessions/{session_id}/upload")
async def api_upload(session_id: str, kind: str, file: UploadFile = File(...)) -> dict:
    _get_session_or_404(session_id)
    if kind not in UPLOAD_KINDS:
        raise HTTPException(status_code=400, detail=f"unsupported upload kind: {kind}")
    data = await file.read()
    try:
        record = controller.register_upload(session_id, kind, file.filename or "upload", data)
    except file_service.FileServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except controller.ChatControllerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return record.to_dict()


@router.get("/api/sessions/{session_id}/audio")
def api_audio(session_id: str) -> FileResponse:
    app = _get_session_or_404(session_id)
    if not app.episode_session_id:
        raise HTTPException(status_code=404, detail="no generated audio for this session")
    try:
        ep = episode.load_session(app.episode_session_id)
    except episode.EpisodeBuildError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    final_audio = ep.artifact_paths.get("final_audio")
    if not final_audio or not Path(str(final_audio)).is_file():
        raise HTTPException(status_code=404, detail="audio is not ready yet")
    return FileResponse(str(final_audio), media_type="audio/mpeg", filename=f"{session_id}.mp3")

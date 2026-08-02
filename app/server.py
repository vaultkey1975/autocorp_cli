"""FastAPI application factory for the AutoCorp Chat App."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import config
from app.routes import router

_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    config.ensure_dirs()
    Path(config.app_sessions_dir()).mkdir(parents=True, exist_ok=True)
    Path(config.app_uploads_dir()).mkdir(parents=True, exist_ok=True)
    application = FastAPI(title="AutoCorp Chat", version=config.APP_VERSION)
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    return application


app = create_app()

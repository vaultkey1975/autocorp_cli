"""Pydantic request bodies for the AutoCorp Chat App API."""

from __future__ import annotations

from pydantic import BaseModel


class SessionCreateRequest(BaseModel):
    message: str = ""
    repo_path: str | None = None


class AnswerRequest(BaseModel):
    value: str | None = None
    text: str | None = None
    upload_id: str | None = None
    confirm_draft: bool | None = None

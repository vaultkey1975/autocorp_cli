"""Managed upload storage for research and approved-script files.

Every upload is copied byte-for-byte into a per-session managed directory
under ``config.app_uploads_dir()``. The original filename is preserved for
display only; the on-disk name is a generated, collision-free name so a
malicious filename can never escape the managed directory or overwrite
another file.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import config

RESEARCH_EXTENSIONS = {".pdf", ".txt"}
SCRIPT_EXTENSIONS = {".txt"}

ALLOWED_EXTENSIONS = {
    "research": RESEARCH_EXTENSIONS,
    "script": SCRIPT_EXTENSIONS,
}


class FileServiceError(ValueError):
    """Raised for a rejected upload (bad extension, too large, unsafe name)."""


@dataclass
class SavedUpload:
    original_filename: str
    managed_path: str
    sha256: str
    size_bytes: int


def _safe_component(session_id: str) -> str:
    if not session_id or not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        raise FileServiceError("invalid session id")
    return session_id


def managed_dir_for_session(session_id: str) -> Path:
    base = Path(config.app_uploads_dir()).resolve()
    target = (base / _safe_component(session_id)).resolve()
    if base not in target.parents and target != base:
        raise FileServiceError("resolved upload directory escapes the managed area")
    return target


def _sanitize_display_name(filename: str) -> str:
    # Display-only: strip any path components an untrusted client may send.
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = name.strip() or "upload"
    return name[:255]


def save_upload(session_id: str, kind: str, filename: str, data: bytes) -> SavedUpload:
    if kind not in ALLOWED_EXTENSIONS:
        raise FileServiceError(f"unsupported upload kind: {kind}")
    if len(data) == 0:
        raise FileServiceError("uploaded file is empty")
    if len(data) > config.APP_UPLOAD_MAX_BYTES:
        raise FileServiceError(
            f"uploaded file exceeds the {config.APP_UPLOAD_MAX_BYTES // (1024 * 1024)}MB limit"
        )
    display_name = _sanitize_display_name(filename)
    ext = Path(display_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS[kind]:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS[kind]))
        raise FileServiceError(f"unsupported file type {ext or '(none)'}; allowed types: {allowed}")

    directory = managed_dir_for_session(session_id)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(display_name).stem)[:80] or "file"
    managed_name = f"{kind}-{uuid.uuid4().hex}-{safe_stem}{ext}"
    managed_path = directory / managed_name
    if managed_path.exists():
        # uuid4 collision is not realistically possible, but never overwrite.
        raise FileServiceError("managed upload path collision; retry the upload")

    tmp_path = directory / f".tmp-{uuid.uuid4().hex}"
    with open(tmp_path, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, managed_path)

    stored = managed_path.read_bytes()
    stored_digest = hashlib.sha256(stored).hexdigest()
    if stored_digest != digest:
        managed_path.unlink(missing_ok=True)
        raise FileServiceError("upload verification failed after write; upload was discarded")

    return SavedUpload(
        original_filename=display_name,
        managed_path=str(managed_path),
        sha256=digest,
        size_bytes=len(data),
    )


def is_path_within_managed_area(path: str) -> bool:
    """True only if ``path`` resolves inside the managed uploads directory
    tree. Used to keep the browser audio/file-serving routes from exposing
    arbitrary local files."""
    base = Path(config.app_uploads_dir()).resolve()
    try:
        candidate = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    return candidate == base or base in candidate.parents

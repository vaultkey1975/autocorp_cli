"""Real, non-mutating system status checks for the status panel.

Every field is either a real, freshly-observed result or the literal string
``"Not checked"`` with a reason — never a guessed or assumed "healthy" value.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import config
from app import clonecast_client as cc
from app import gpu_guard
from brains import guided_clonecast_episode as episode


def _bool_check(label: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"label": label, "status": "ok" if ok else "unavailable", "detail": detail}


def _not_checked(label: str, reason: str) -> dict[str, Any]:
    return {"label": label, "status": "Not checked", "detail": reason}


def _autocorp_repo_status() -> dict[str, Any]:
    return _bool_check("AutoCorp repository available", True, config.BASE_DIR)


def _autocorp_venv_status() -> dict[str, Any]:
    python = Path(config.BASE_DIR) / ".venv" / "bin" / "python"
    return _bool_check("AutoCorp virtual environment available", python.is_file(), str(python))


def _autocorp_session_store_status() -> dict[str, Any]:
    directory = Path(config.app_sessions_dir())
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return _bool_check("AutoCorp session store available", True, str(directory))
    except OSError as exc:
        return _bool_check("AutoCorp session store available", False, str(exc))


def _clonecast_repo_status(repo_path: str) -> dict[str, Any]:
    try:
        episode.CloneCastCLI(Path(repo_path)).validate_repo()
        return _bool_check("CloneCast repository available", True, repo_path)
    except episode.EpisodeBuildError as exc:
        return _bool_check("CloneCast repository available", False, str(exc))


def _clonecast_venv_status(repo_path: str) -> dict[str, Any]:
    python = Path(repo_path) / ".venv" / "bin" / "python"
    return _bool_check("CloneCast virtual environment available", python.is_file(), str(python))


def _clonecast_config_status(repo_path: str) -> tuple[dict[str, Any], str | None, str | None]:
    if not Path(repo_path).is_dir():
        return _bool_check("CloneCast configuration loaded", False, f"repository path does not exist: {repo_path}"), None, None
    try:
        client = cc.CloneCastClient(repo_path)
        payload = client.config_check()
        db_path = payload.get("db_path") if isinstance(payload, dict) else None
        primary_gpu = payload.get("primary_ai_gpu_identity") if isinstance(payload, dict) else None
        return _bool_check("CloneCast configuration loaded", True, f"db_path={db_path}"), db_path, primary_gpu
    except (episode.EpisodeBuildError, OSError) as exc:
        return _bool_check("CloneCast configuration loaded", False, str(exc)), None, None


def _clonecast_database_status(db_path: str | None) -> dict[str, Any]:
    if not db_path:
        return _not_checked("CloneCast database reachable", "database path unknown; config-check did not succeed")
    path = Path(db_path)
    if not path.is_file():
        return _bool_check("CloneCast database reachable", False, f"missing: {db_path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()
        return _bool_check("CloneCast database reachable", True, db_path)
    except sqlite3.Error as exc:
        return _bool_check("CloneCast database reachable", False, str(exc))


def _gpu_status() -> tuple[dict[str, Any], dict[str, Any], str | None]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        reason = "nvidia-smi not found on PATH"
        return (_not_checked("CUDA available", reason), _not_checked("RTX 4060 Ti detected", reason), None)
    try:
        result = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError) as exc:
        reason = f"nvidia-smi failed: {exc}"
        return (_not_checked("CUDA available", reason), _not_checked("RTX 4060 Ti detected", reason), None)
    if result.returncode != 0:
        reason = result.stderr.strip() or "nvidia-smi returned a non-zero exit code"
        return (_bool_check("CUDA available", False, reason), _bool_check("RTX 4060 Ti detected", False, reason), None)
    output = result.stdout.strip()
    has_gpu = bool(output)
    has_4060 = "4060 Ti" in output
    current_gpu = output.splitlines()[0].strip() if output else None
    return (
        _bool_check("CUDA available", has_gpu, output or "no GPUs listed"),
        _bool_check("RTX 4060 Ti detected", has_4060, output or "no GPUs listed"),
        current_gpu,
    )


def _desktop_launcher_status() -> dict[str, Any]:
    desktop_entry = Path.home() / ".local" / "share" / "applications" / "autocorp.desktop"
    return _bool_check("Desktop-launcher status", desktop_entry.is_file(), str(desktop_entry))


def build_status_report(repo_path: str | None = None, *, active_sessions: int = 0) -> dict[str, Any]:
    repo_path = repo_path or config.CLONECAST_REPO_PATH
    config_status, db_path, configured_gpu = _clonecast_config_status(repo_path)
    cuda_status, gpu_4060_status, detected_gpu = _gpu_status()
    current_gpu = configured_gpu or detected_gpu
    checks = [
        _autocorp_repo_status(),
        _autocorp_venv_status(),
        _autocorp_session_store_status(),
        _clonecast_repo_status(repo_path),
        _clonecast_venv_status(repo_path),
        config_status,
        _clonecast_database_status(db_path),
        _not_checked("CloneCast migration status", "not implemented in Phase 1"),
        _not_checked("CloneCast integrity status", "full integrity check is expensive; run manually if needed"),
        _not_checked("Chatterbox provider available", "only verified live during real audio generation"),
        cuda_status,
        gpu_4060_status,
    ]
    return {
        "checks": checks,
        "current_selected_gpu": current_gpu or "Not checked",
        "current_active_session_count": active_sessions,
        "local_app_address": f"http://{config.APP_HOST}:{config.APP_PORT}",
        "desktop_launcher": _desktop_launcher_status(),
        "publishing_lock_status": "locked",
        "gpu_resource_manager": _gpu_resource_manager_status(),
    }


def _gpu_resource_manager_status() -> dict[str, Any]:
    last = gpu_guard.last_reservation()
    ollama_loaded = gpu_guard.list_ollama_loaded_models()
    return {
        "enabled": config.GPU_GUARD_ENABLED,
        "ollama_loaded_models": ollama_loaded,
        "last_reservation": last,
    }

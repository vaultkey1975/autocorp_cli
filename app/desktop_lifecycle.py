"""Local desktop lifecycle management for AutoCorp.

The desktop wrapper owns the server process it starts. On close it asks the
running app to cancel active work, then terminates the server process group so
AutoCorp-owned CloneCast/Chatterbox children do not survive the window.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import config
from app import gpu_guard
from app import launcher


@dataclass
class ShutdownResult:
    active_job_detected: bool
    cancellation_requested: bool
    cancellation_result: dict[str, Any] | None
    child_cleanup_result: str
    gpu_release_result: dict[str, Any] | None
    port_released: bool
    pid_files_removed: bool
    final_result: str
    logs: list[str] = field(default_factory=list)


def log(message: str) -> None:
    launcher._log(message)


def desktop_pid_file() -> Path:
    return Path(config.APP_DESKTOP_PID_FILE)


def read_desktop_pid() -> int | None:
    path = desktop_pid_file()
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_desktop_pid(pid: int | None = None) -> None:
    path = desktop_pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid or os.getpid()), encoding="utf-8")


def remove_state_files() -> bool:
    ok = True
    for filename in (config.APP_PID_FILE, config.APP_DESKTOP_PID_FILE, config.APP_LOCK_FILE):
        try:
            Path(filename).unlink(missing_ok=True)
        except OSError:
            ok = False
    return ok


def desktop_instance_running() -> bool:
    pid = read_desktop_pid()
    return bool(pid and launcher._pid_is_running(pid))


def focus_existing_window() -> bool:
    """Best-effort focus for the already-open desktop window."""
    tool = shutil.which("wmctrl")
    if not tool:
        log("desktop focus requested; wmctrl unavailable")
        return False
    try:
        subprocess.run([tool, "-a", "AutoCorp"], check=False, timeout=2)
    except (OSError, subprocess.SubprocessError):
        log("desktop focus requested; wmctrl failed")
        return False
    log("desktop focus requested")
    return True


def launch_server(
    *,
    host: str = config.APP_HOST,
    port: int = config.APP_PORT,
    readiness_timeout: float = 30.0,
) -> launcher.LaunchResult:
    log("application start requested")
    if desktop_instance_running() and launcher.is_server_ready(host, port):
        focus_existing_window()
        pid = launcher._read_pid_file()
        return launcher.LaunchResult(False, True, pid, f"http://{host}:{port}", "Focused existing AutoCorp window.")
    result = launcher.start_or_connect(
        host=host,
        port=port,
        open_browser=False,
        readiness_timeout=readiness_timeout,
    )
    if result.pid:
        log(f"server PID {result.pid}")
    return result


def api_request(host: str, port: int, method: str, path: str, *, timeout: float = 3.0) -> dict[str, Any]:
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request(method, path, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        payload = json.loads(body) if body else {}
        if resp.status >= 400:
            return {"ok": False, "status": resp.status, "payload": payload}
        return {"ok": True, "status": resp.status, "payload": payload}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": None, "payload": {"error": str(exc)}}
    finally:
        conn.close()


def active_generation_status(host: str = config.APP_HOST, port: int = config.APP_PORT) -> dict[str, Any]:
    result = api_request(host, port, "GET", "/api/lifecycle/active")
    if result.get("ok"):
        return dict(result["payload"])
    return {"active": False, "sessions": [], "error": result.get("payload")}


def cancel_active_generation(host: str = config.APP_HOST, port: int = config.APP_PORT) -> dict[str, Any]:
    result = api_request(host, port, "POST", "/api/lifecycle/cancel-active", timeout=15.0)
    return {"ok": bool(result.get("ok")), "status": result.get("status"), "payload": result.get("payload")}


def port_released(host: str = config.APP_HOST, port: int = config.APP_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return False
    except OSError:
        return True


def wait_for_port_release(host: str = config.APP_HOST, port: int = config.APP_PORT, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_released(host, port):
            return True
        time.sleep(0.2)
    return port_released(host, port)


def _terminate_process_group(pid: int | None, *, timeout: float) -> str:
    if not pid or not launcher._pid_is_running(pid):
        return "no running server process"
    if launcher.stop_server(pid, timeout=timeout):
        return "terminated"
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        if pgid:
            os.killpg(pgid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "terminated after grace period"
    except OSError as exc:
        return f"kill failed: {exc}"
    return "killed after grace period"


def shutdown_desktop(
    *,
    host: str = config.APP_HOST,
    port: int = config.APP_PORT,
    cancel_active: bool = False,
    pid: int | None = None,
    terminate: Callable[[int | None], str] | None = None,
) -> ShutdownResult:
    log("close requested")
    status = active_generation_status(host, port)
    active = bool(status.get("active"))
    cancellation = None
    if active:
        log("active job detected")
    if active and cancel_active:
        log("active job cancellation requested")
        cancellation = cancel_active_generation(host, port)
        log(f"cancellation result {json.dumps(cancellation, sort_keys=True, default=str)}")

    reservation = gpu_guard.release_stage(
        "AutoCorp desktop shutdown",
        gpu_name_substring=config.CHATTERBOX_GPU_NAME_SUBSTRING,
        output=lambda event, text: log(f"{event}: {text}"),
    )
    gpu_result = reservation.to_dict()
    log(f"GPU release result {json.dumps(gpu_result, sort_keys=True, default=str)}")

    server_pid = pid if pid is not None else launcher._read_pid_file()
    cleanup = terminate(server_pid) if terminate else _terminate_process_group(server_pid, timeout=10.0)
    log(f"child-process cleanup {cleanup}")
    released = wait_for_port_release(host, port)
    log(f"port release result {released}")
    removed = remove_state_files()
    final = "shutdown complete" if released and removed else "shutdown incomplete"
    log(f"final shutdown result {final}")
    return ShutdownResult(
        active_job_detected=active,
        cancellation_requested=bool(active and cancel_active),
        cancellation_result=cancellation,
        child_cleanup_result=cleanup,
        gpu_release_result=gpu_result,
        port_released=released,
        pid_files_removed=removed,
        final_result=final,
    )

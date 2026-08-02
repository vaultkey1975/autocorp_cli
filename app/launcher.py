"""Desktop launcher logic for the AutoCorp Chat App.

Single-instance, readiness-gated startup used by ``scripts/start_autocorp_app.sh``
and directly importable for tests. Never launches a second server if one is
already listening and healthy; never opens the browser before the server has
proven it is actually ready to serve requests.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import config

_SERVER_PROCESSES: dict[int, subprocess.Popen] = {}


@dataclass
class LaunchResult:
    started_new_process: bool
    ready: bool
    pid: int | None
    url: str
    message: str


def _log(message: str) -> None:
    log_dir = Path(config.APP_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n"
    with open(log_dir / "launcher.log", "a", encoding="utf-8") as fh:
        fh.write(line)


def is_server_ready(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        if resp.status != 200:
            return False
        payload = json.loads(resp.read().decode("utf-8"))
        return bool(payload.get("server_ready"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _read_pid_file() -> int | None:
    path = Path(config.APP_PID_FILE)
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by someone else; treat it as
        # running for single-instance purposes.
        return True
    except OSError:
        return False
    return True


def _write_pid_file(pid: int) -> None:
    Path(config.APP_PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.APP_PID_FILE).write_text(str(pid), encoding="utf-8")


def remove_stale_pid_file() -> None:
    pid = _read_pid_file()
    if pid is None or not _pid_is_running(pid):
        Path(config.APP_PID_FILE).unlink(missing_ok=True)


def is_port_released(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return False
    except OSError:
        return True


def stop_server(pid: int | None = None, *, timeout: float = 10.0) -> bool:
    pid = pid if pid is not None else _read_pid_file()
    if not pid or not _pid_is_running(pid):
        Path(config.APP_PID_FILE).unlink(missing_ok=True)
        return True
    proc = _SERVER_PROCESSES.get(pid)
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        if pgid:
            os.killpg(pgid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        Path(config.APP_PID_FILE).unlink(missing_ok=True)
        return True

    if proc is not None:
        try:
            proc.wait(timeout=timeout)
            _SERVER_PROCESSES.pop(pid, None)
            Path(config.APP_PID_FILE).unlink(missing_ok=True)
            return True
        except subprocess.TimeoutExpired:
            return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_is_running(pid):
            Path(config.APP_PID_FILE).unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    return False


def start_or_connect(
    *,
    host: str = config.APP_HOST,
    port: int = config.APP_PORT,
    repo_root: str | None = None,
    open_browser: bool = True,
    readiness_timeout: float = 30.0,
    launcher_python: str | None = None,
) -> LaunchResult:
    """Idempotent single-instance startup. Safe to call repeatedly (e.g. on
    every desktop double-click): if a healthy server is already listening,
    it is reused and no second process is started."""
    repo_root = repo_root or config.BASE_DIR
    url = f"http://{host}:{port}"

    if is_server_ready(host, port):
        _log(f"server already running and ready at {url}; reusing it")
        if open_browser:
            webbrowser.open(url)
        return LaunchResult(
            started_new_process=False,
            ready=True,
            pid=_read_pid_file(),
            url=url,
            message="Reused running server.",
        )

    existing_pid = _read_pid_file()
    if existing_pid and not _pid_is_running(existing_pid):
        _log(f"removing stale PID file for non-running pid {existing_pid}")
        remove_stale_pid_file()
        existing_pid = None
    if existing_pid and _pid_is_running(existing_pid) and not is_server_ready(host, port):
        # Stale-ish: a process with this PID exists but isn't answering yet,
        # or the PID file is stale from a crashed process. Give it a short
        # grace period rather than assuming it is safe to start a duplicate.
        deadline = time.time() + 3
        while time.time() < deadline:
            if is_server_ready(host, port):
                if open_browser:
                    webbrowser.open(url)
                return LaunchResult(True, True, existing_pid, url, "Reused starting server.")
            time.sleep(0.3)

    python = launcher_python or str(Path(repo_root) / ".venv" / "bin" / "python")
    if not Path(python).is_file():
        message = f"AutoCorp virtual environment python not found: {python}"
        _log(f"STARTUP FAILED: {message}")
        return LaunchResult(False, False, None, url, message)

    autocorp_script = str(Path(repo_root) / "autocorp.py")
    log_dir = Path(config.APP_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = open(log_dir / "server.out.log", "a", encoding="utf-8")
    stderr_log = open(log_dir / "server.err.log", "a", encoding="utf-8")
    _log(f"starting server: {python} {autocorp_script} app --host {host} --port {port}")
    try:
        proc = subprocess.Popen(
            [python, autocorp_script, "app", "--host", host, "--port", str(port)],
            cwd=repo_root,
            stdout=stdout_log,
            stderr=stderr_log,
            start_new_session=True,
        )
    except OSError as exc:
        message = f"failed to start AutoCorp server process: {exc}"
        _log(f"STARTUP FAILED: {message}")
        return LaunchResult(False, False, None, url, message)
    finally:
        stdout_log.close()
        stderr_log.close()

    _SERVER_PROCESSES[proc.pid] = proc
    _write_pid_file(proc.pid)
    _log(f"server process started with pid {proc.pid}; waiting for readiness")

    deadline = time.time() + readiness_timeout
    while time.time() < deadline:
        if is_server_ready(host, port):
            _log(f"server ready at {url}")
            if open_browser:
                webbrowser.open(url)
            return LaunchResult(True, True, proc.pid, url, "Started AutoCorp and opened the browser.")
        if proc.poll() is not None:
            _SERVER_PROCESSES.pop(proc.pid, None)
            message = (
                f"AutoCorp server process exited early with code {proc.returncode}; "
                f"see {log_dir / 'server.err.log'}"
            )
            _log(f"STARTUP FAILED: {message}")
            return LaunchResult(True, False, proc.pid, url, message)
        time.sleep(0.3)

    message = f"AutoCorp server did not become ready within {readiness_timeout}s; see {log_dir / 'launcher.log'}"
    _log(f"STARTUP FAILED: {message}")
    return LaunchResult(True, False, proc.pid, url, message)


def main() -> int:
    result = start_or_connect()
    if not result.ready:
        print(result.message, file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

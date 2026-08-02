import os
import signal
import socket
import time
from pathlib import Path

import pytest

import config
from app import launcher


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def isolated_launcher_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_PID_FILE", str(tmp_path / "app.pid"))
    monkeypatch.setattr(config, "APP_LOCK_FILE", str(tmp_path / "app.lock"))
    monkeypatch.setattr(config, "APP_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def _kill(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
    except ProcessLookupError:
        pass


def test_launcher_starts_real_app_waits_for_readiness_then_opens_browser(isolated_launcher_paths, monkeypatch):
    # Runs outside the repository directory to prove the launcher does not
    # rely on the caller's working directory.
    monkeypatch.chdir(isolated_launcher_paths)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    port = _free_port()
    result = launcher.start_or_connect(host="127.0.0.1", port=port, open_browser=True, readiness_timeout=30)
    try:
        assert result.started_new_process is True
        assert result.ready is True
        assert launcher.is_server_ready("127.0.0.1", port)
        assert opened == [f"http://127.0.0.1:{port}"]
        assert Path(config.APP_PID_FILE).read_text(encoding="utf-8").strip() == str(result.pid)
    finally:
        _kill(result.pid)


def test_second_launch_reuses_running_server_without_duplicate_process(isolated_launcher_paths, monkeypatch):
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    port = _free_port()
    first = launcher.start_or_connect(host="127.0.0.1", port=port, open_browser=False, readiness_timeout=30)
    try:
        assert first.ready
        second = launcher.start_or_connect(host="127.0.0.1", port=port, open_browser=True, readiness_timeout=5)
        assert second.started_new_process is False
        assert second.ready is True
        assert second.pid == first.pid
    finally:
        _kill(first.pid)


def test_stale_pid_file_does_not_block_a_fresh_start(isolated_launcher_paths, monkeypatch):
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    Path(config.APP_PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.APP_PID_FILE).write_text("999999999", encoding="utf-8")  # not a real PID
    port = _free_port()
    result = launcher.start_or_connect(host="127.0.0.1", port=port, open_browser=False, readiness_timeout=30)
    try:
        assert result.ready is True
    finally:
        _kill(result.pid)


def test_startup_failure_writes_log_and_returns_user_visible_message(isolated_launcher_paths, monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    port = _free_port()
    result = launcher.start_or_connect(
        host="127.0.0.1",
        port=port,
        open_browser=True,
        readiness_timeout=2,
        launcher_python="/nonexistent/does-not-exist/python",
    )
    assert result.ready is False
    assert "not found" in result.message
    assert opened == []  # never open the browser before readiness
    log_path = Path(config.APP_LOG_DIR) / "launcher.log"
    assert log_path.is_file()
    assert "STARTUP FAILED" in log_path.read_text(encoding="utf-8")


def test_desktop_entry_uses_absolute_exec_and_icon_paths():
    content = Path(config.BASE_DIR, "desktop", "autocorp.desktop").read_text(encoding="utf-8")
    exec_line = next(line for line in content.splitlines() if line.startswith("Exec="))
    icon_line = next(line for line in content.splitlines() if line.startswith("Icon="))
    exec_path = exec_line.split("=", 1)[1]
    icon_path = icon_line.split("=", 1)[1]
    assert Path(exec_path).is_absolute()
    assert Path(icon_path).is_absolute()
    assert Path(exec_path).is_file()
    assert Path(icon_path).is_file()


def test_start_script_resolves_repo_root_from_its_own_location_not_cwd():
    content = Path(config.BASE_DIR, "scripts", "start_autocorp_app.sh").read_text(encoding="utf-8")
    assert "BASH_SOURCE" in content
    assert ".venv/bin/python" in content


def test_installer_is_idempotent_and_requires_no_sudo():
    content = Path(config.BASE_DIR, "scripts", "install_autocorp_desktop.sh").read_text(encoding="utf-8")
    assert "sudo " not in content and not content.strip().startswith("sudo")

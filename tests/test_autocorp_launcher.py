import os
import signal
import socket
import time
from pathlib import Path

import pytest

import config
from app import desktop_lifecycle
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
    monkeypatch.setattr(config, "APP_DESKTOP_PID_FILE", str(tmp_path / "desktop.pid"))
    monkeypatch.setattr(config, "APP_DESKTOP_FOCUS_FILE", str(tmp_path / "focus.request"))
    monkeypatch.setattr(config, "APP_LOCK_FILE", str(tmp_path / "app.lock"))
    monkeypatch.setattr(config, "APP_LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


def _kill(pid: int | None) -> None:
    if not pid:
        return
    if launcher.stop_server(pid, timeout=5):
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


def test_desktop_launch_starts_one_server_without_opening_browser(isolated_launcher_paths, monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))
    port = _free_port()
    result = desktop_lifecycle.launch_server(host="127.0.0.1", port=port, readiness_timeout=30)
    try:
        assert result.started_new_process is True
        assert result.ready is True
        assert launcher.is_server_ready("127.0.0.1", port)
        assert opened == []
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


def test_desktop_second_launch_focuses_existing_window_without_duplicate(isolated_launcher_paths, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_lifecycle, "focus_existing_window", lambda: calls.append("focus") or True)
    monkeypatch.setattr(launcher, "is_server_ready", lambda host, port: True)
    desktop_lifecycle.write_desktop_pid(os.getpid())
    Path(config.APP_PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.APP_PID_FILE).write_text(str(os.getpid()), encoding="utf-8")

    result = desktop_lifecycle.launch_server(host="127.0.0.1", port=8787)

    assert result.started_new_process is False
    assert result.ready is True
    assert calls == ["focus"]


def test_focus_existing_window_restores_minimized_window_and_raises(isolated_launcher_paths, monkeypatch):
    commands = []

    class Result:
        returncode = 0

    monkeypatch.setattr(desktop_lifecycle.shutil, "which", lambda name: "/usr/bin/wmctrl")
    monkeypatch.setattr(
        desktop_lifecycle.subprocess,
        "run",
        lambda command, check, timeout: commands.append(command) or Result(),
    )

    assert desktop_lifecycle.focus_existing_window()

    assert [command[1:] for command in commands] == [
        ["-r", "AutoCorp", "-b", "remove,hidden"],
        ["-r", "AutoCorp", "-b", "remove,shaded"],
        ["-R", "AutoCorp"],
        ["-a", "AutoCorp"],
    ]


def test_focus_existing_window_writes_wayland_fallback_request(isolated_launcher_paths, monkeypatch):
    monkeypatch.setattr(desktop_lifecycle.shutil, "which", lambda name: None)

    assert desktop_lifecycle.focus_existing_window() is False

    assert Path(config.APP_DESKTOP_FOCUS_FILE).is_file()


def test_first_desktop_launch_schedules_qt_focus_after_window_show():
    content = Path(config.BASE_DIR, "app", "desktop_app.py").read_text(encoding="utf-8")
    assert "QTimer.singleShot(250, window.bring_to_front)" in content
    assert "self.showNormal()" in content
    assert "self.raise_()" in content
    assert "self.activateWindow()" in content


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


def test_desktop_close_stops_server_and_releases_port(isolated_launcher_paths, monkeypatch):
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: None)
    port = _free_port()
    launch = desktop_lifecycle.launch_server(host="127.0.0.1", port=port, readiness_timeout=30)
    assert launch.ready

    result = desktop_lifecycle.shutdown_desktop(host="127.0.0.1", port=port, pid=launch.pid)

    assert result.final_result == "shutdown complete"
    assert result.port_released is True
    assert not Path(config.APP_PID_FILE).exists()
    assert not Path(config.APP_DESKTOP_PID_FILE).exists()


def test_child_process_cleanup_is_requested_with_server_pid(isolated_launcher_paths, monkeypatch):
    Path(config.APP_PID_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(config.APP_PID_FILE).write_text("12345", encoding="utf-8")
    seen = []
    monkeypatch.setattr(
        desktop_lifecycle,
        "active_generation_status",
        lambda host, port: {"active": False, "sessions": []},
    )
    monkeypatch.setattr(desktop_lifecycle, "wait_for_port_release", lambda host, port: True)

    result = desktop_lifecycle.shutdown_desktop(terminate=lambda pid: seen.append(pid) or "terminated")

    assert seen == [12345]
    assert result.child_cleanup_result == "terminated"


def test_gpu_reservation_cleanup_is_recorded(isolated_launcher_paths, monkeypatch):
    class Reservation:
        def to_dict(self):
            return {"ok": True, "stage": "AutoCorp desktop shutdown (release)"}

    monkeypatch.setattr(
        desktop_lifecycle,
        "active_generation_status",
        lambda host, port: {"active": False, "sessions": []},
    )
    monkeypatch.setattr(desktop_lifecycle, "wait_for_port_release", lambda host, port: True)
    monkeypatch.setattr(desktop_lifecycle.gpu_guard, "release_stage", lambda *a, **k: Reservation())

    result = desktop_lifecycle.shutdown_desktop(pid=None, terminate=lambda pid: "no running server process")

    assert result.gpu_release_result["ok"] is True
    assert "release" in result.gpu_release_result["stage"]


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
    assert "Terminal=false" in content


def test_start_script_resolves_repo_root_from_its_own_location_not_cwd():
    content = Path(config.BASE_DIR, "scripts", "start_autocorp_app.sh").read_text(encoding="utf-8")
    assert "BASH_SOURCE" in content
    assert ".venv/bin/python" in content
    assert "-m app.desktop_app" in content
    assert "-m app.launcher" not in content
    assert 'export QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-gpu-compositing"' in content
    assert "export QT_QUICK_BACKEND=software" in content


def test_desktop_wrapper_warns_before_closing_active_generation():
    content = Path(config.BASE_DIR, "app", "desktop_app.py").read_text(encoding="utf-8")
    assert "active_generation_status" in content
    assert "Keep AutoCorp open" in content
    assert "Cancel active job and close" in content
    assert "shutdown_desktop(cancel_active=cancel" in content


def test_installer_is_idempotent_and_requires_no_sudo():
    content = Path(config.BASE_DIR, "scripts", "install_autocorp_desktop.sh").read_text(encoding="utf-8")
    assert "sudo " not in content and not content.strip().startswith("sudo")

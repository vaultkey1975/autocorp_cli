from app import system_status


def test_missing_clonecast_path_is_reported_clearly(tmp_path):
    report = system_status.build_status_report(repo_path=str(tmp_path / "does-not-exist"))
    clonecast_check = next(c for c in report["checks"] if c["label"] == "CloneCast repository available")
    assert clonecast_check["status"] == "unavailable"
    assert "does not exist" in clonecast_check["detail"] or "invalid" in clonecast_check["detail"]


def test_missing_clonecast_path_does_not_report_database_as_healthy(tmp_path):
    report = system_status.build_status_report(repo_path=str(tmp_path / "does-not-exist"))
    db_check = next(c for c in report["checks"] if c["label"] == "CloneCast database reachable")
    assert db_check["status"] != "ok"


def test_unperformed_checks_are_explicitly_not_checked(tmp_path):
    report = system_status.build_status_report(repo_path=str(tmp_path / "does-not-exist"))
    migration_check = next(c for c in report["checks"] if c["label"] == "CloneCast migration status")
    assert migration_check["status"] == "Not checked"
    chatterbox_check = next(c for c in report["checks"] if c["label"] == "Chatterbox provider available")
    assert chatterbox_check["status"] == "Not checked"


def test_publishing_lock_status_is_always_visible_and_locked():
    report = system_status.build_status_report()
    assert report["publishing_lock_status"] == "locked"


def test_status_report_never_calls_a_publishing_command(monkeypatch, tmp_path):
    # Regression guard: system_status must stay strictly read-only.
    import subprocess

    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("args"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    system_status.build_status_report(repo_path=str(tmp_path / "does-not-exist"))
    for call in calls:
        assert not any("publi" in str(part).lower() for part in call)


def test_gpu_status_reflects_real_nvidia_smi_output_not_a_guess(monkeypatch):
    import subprocess

    class FakeResult:
        returncode = 0
        stdout = "GPU 0: NVIDIA GeForce RTX 4060 Ti (UUID: GPU-fake)\n"
        stderr = ""

    monkeypatch.setattr(system_status.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeResult())
    cuda, rtx, gpu = system_status._gpu_status()
    assert cuda["status"] == "ok"
    assert rtx["status"] == "ok"
    assert "4060 Ti" in gpu


def test_gpu_status_is_not_checked_when_nvidia_smi_is_absent(monkeypatch):
    monkeypatch.setattr(system_status.shutil, "which", lambda name: None)
    cuda, rtx, gpu = system_status._gpu_status()
    assert cuda["status"] == "Not checked"
    assert rtx["status"] == "Not checked"
    assert gpu is None

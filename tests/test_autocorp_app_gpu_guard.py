import re
from pathlib import Path

import pytest

import config
from app import chat_controller as controller
from app import gpu_guard
from app import session_store as store
from brains import guided_clonecast_episode as episode
from tests._fake_clonecast import VOICE_LARRY_APPROVED, FakeCloneCastCLI, make_repo


class FakeSMI:
    """Stands in for `nvidia-smi` output across a sequence of calls, so a
    test can simulate VRAM being freed after an unload."""

    def __init__(self, free_sequence: list[int], gpu_name: str = "NVIDIA GeForce RTX 4060 Ti"):
        self.free_sequence = list(free_sequence)
        self.gpu_name = gpu_name
        self.calls = 0

    def __call__(self, args, timeout=5):
        free = self.free_sequence[min(self.calls, len(self.free_sequence) - 1)]
        self.calls += 1
        if "--query-compute-apps" in args[0]:
            return "1234, 9000, ollama\n"
        return f"0, {self.gpu_name}, {free}, 16380\n"


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "APP_LOG_DIR", str(tmp_path / "data" / "logs"))
    return tmp_path


def test_sufficient_vram_skips_ollama_unload_entirely(isolated_logs, monkeypatch):
    calls = {"unload": 0}
    monkeypatch.setattr(gpu_guard, "_run_nvidia_smi", lambda args, timeout=5: "0, RTX 4060 Ti, 16000, 16380\n")
    monkeypatch.setattr(gpu_guard, "list_ollama_loaded_models", lambda: ["qwen2.5:14b"])
    monkeypatch.setattr(gpu_guard, "unload_ollama_model", lambda m, timeout=10: calls.__setitem__("unload", calls["unload"] + 1) or True)
    events = []
    reservation = gpu_guard.reserve_for_stage(
        "test-stage", required_mb=8512, gpu_name_substring="RTX 4060 Ti", output=lambda e, t: events.append((e, t))
    )
    assert reservation.ok
    assert calls["unload"] == 0
    assert events[0][0] == "ollama_not_required"


def test_insufficient_vram_unloads_ollama_and_verifies_real_release(isolated_logs, monkeypatch):
    smi = FakeSMI(free_sequence=[5000, 5000, 9000])  # frees up after unload + one poll
    monkeypatch.setattr(gpu_guard, "_run_nvidia_smi", smi)
    unloaded = []
    monkeypatch.setattr(gpu_guard, "list_ollama_loaded_models", lambda: ["qwen2.5:14b"])
    monkeypatch.setattr(gpu_guard, "unload_ollama_model", lambda m, timeout=10: unloaded.append(m) or True)
    events = []
    reservation = gpu_guard.reserve_for_stage(
        "test-stage", required_mb=8512, gpu_name_substring="RTX 4060 Ti",
        output=lambda e, t: events.append((e, t)), poll_seconds=0.01, max_wait_seconds=1,
    )
    assert reservation.ok
    assert unloaded == ["qwen2.5:14b"]
    assert reservation.free_mb_after == 9000
    event_names = [e for e, _ in events]
    assert event_names == ["unloading_ollama", "waiting_for_gpu_memory", "gpu_reserved"]


def test_still_insufficient_after_unload_fails_safely_not_fake_success(isolated_logs, monkeypatch):
    monkeypatch.setattr(gpu_guard, "_run_nvidia_smi", FakeSMI(free_sequence=[3000, 3000, 3000]))
    monkeypatch.setattr(gpu_guard, "list_ollama_loaded_models", lambda: ["qwen2.5:14b"])
    monkeypatch.setattr(gpu_guard, "unload_ollama_model", lambda m, timeout=10: True)
    reservation = gpu_guard.reserve_for_stage(
        "test-stage", required_mb=8512, gpu_name_substring="RTX 4060 Ti",
        poll_seconds=0.01, max_wait_seconds=0.05,
    )
    assert not reservation.ok
    assert "3000" in reservation.failure_reason
    assert "8512" in reservation.failure_reason
    assert "ollama" in reservation.failure_reason.lower()  # real process evidence, not a guess


def test_reservation_is_recorded_and_readable(isolated_logs, monkeypatch):
    monkeypatch.setattr(gpu_guard, "_run_nvidia_smi", lambda args, timeout=5: "0, RTX 4060 Ti, 16000, 16380\n")
    monkeypatch.setattr(gpu_guard, "list_ollama_loaded_models", lambda: [])
    gpu_guard.reserve_for_stage("stage-a", required_mb=100, gpu_name_substring="RTX 4060 Ti")
    last = gpu_guard.last_reservation()
    assert last["stage"] == "stage-a"
    assert last["ok"] is True


def test_missing_nvidia_smi_does_not_block_and_is_not_claimed_as_verified(isolated_logs, monkeypatch):
    monkeypatch.setattr(gpu_guard, "_run_nvidia_smi", lambda args, timeout=5: None)
    reservation = gpu_guard.reserve_for_stage("stage-b", required_mb=100, gpu_name_substring="RTX 4060 Ti")
    assert reservation.ok
    assert "unavailable" in reservation.failure_reason


def test_ollama_is_never_imported_by_the_production_research_or_script_path():
    # Structural regression guard for the permanent policy: nothing on the
    # research/script preservation path may call Ollama or any local model.
    for path in ("app/file_service.py", "app/clonecast_client.py", "app/session_store.py"):
        source = Path(config.BASE_DIR, path).read_text(encoding="utf-8")
        assert "ollama" not in source.lower()
        assert "core.llm" not in source


def test_gpu_guard_disabled_by_config_flag_is_a_true_no_op(isolated_logs, monkeypatch):
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", False)
    called = []
    monkeypatch.setattr(gpu_guard, "reserve_for_stage", lambda *a, **k: called.append(1))
    handle = controller.EngineHandle(app_session_id="appsess_x")
    controller._run_gpu_guard("appsess_x", handle)
    assert called == []


def test_gpu_failure_blocks_generation_before_any_speech_call_and_preserves_session(isolated_logs, monkeypatch, tmp_path):
    repo = make_repo(tmp_path)
    monkeypatch.setattr(config, "GPU_GUARD_ENABLED", True)
    monkeypatch.setattr(
        gpu_guard,
        "reserve_for_stage",
        lambda *a, **k: gpu_guard.GPUReservation(
            "Chatterbox audio generation", False, "RTX 4060 Ti", 8512, 3000, 3000,
            failure_reason="only 3000 MiB free; 8512 MiB required",
        ),
    )

    def factory(path):
        return FakeCloneCastCLI(path)

    app = controller.start_session(
        str(repo), "Create a Shadow Frequency episode. 10 minutes. No guests. Audio only.", clonecast_cli_factory=factory
    )
    rec = controller.register_upload(app.session_id, "research", "r.txt", b"research body")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    rec = controller.register_upload(app.session_id, "script", "s.txt", b"Host: hi\n")
    app = controller.submit_answer(app.session_id, {"upload_id": rec.upload_id})
    app = controller.submit_answer(app.session_id, {"text": "Elias Voss"})
    app = controller.submit_answer(app.session_id, {"value": VOICE_LARRY_APPROVED})

    app = controller.submit_answer(app.session_id, {"value": "yes"})

    assert app.status == "failed"
    assert "GPU ran out of memory" in app.error["safe_message"]
    assert app.error["retry_safe"] is True

    ep = episode.load_session(app.episode_session_id)
    called_commands = [c["command"][0] for c in ep.clonecast_commands]
    # episode-create/approved-script-import/speech-text-preview are cheap,
    # non-GPU CloneCast calls that now legitimately run before "Start
    # Generation" is even answered, so the owner's pre-generation review is
    # backed by real data - the GPU guard only gates the actual Chatterbox
    # sequence (speech-provider-check through episode-audio-master), which
    # must never have started.
    assert "episode-create" in called_commands
    assert "speech-text-preview" in called_commands
    assert "speech-render" not in called_commands
    assert "speech-provider-check" not in called_commands
    assert "episode-audio-master" not in called_commands
    assert ep.owner_approval_status == "publishing_locked"

    # Session is genuinely resumable: answers already given before the GPU
    # check (voice selection) survived the failure untouched.
    assert ep.selected_voices.get("host") == VOICE_LARRY_APPROVED

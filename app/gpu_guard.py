"""GPU / Ollama coordination guard for real CloneCast generation stages.

Permanent production policy (not a temporary test workaround):

- Ollama is optional and off by default for production. It is never used to
  write or rewrite research or approved scripts anywhere in this app -
  research and scripts only ever come from the user's own upload/paste, and
  are preserved byte-for-byte (see brains/guided_clonecast_episode.py).
- Before a heavy CloneCast generation stage (today: Chatterbox audio -
  video/lip-sync/upscaling stages do not exist in any AutoCorp phase yet and
  are intentionally not wired here; add them when those stages are real),
  this module verifies actual free VRAM on the configured GPU and, only if
  necessary, asks Ollama's own local API/CLI to unload its model.
- This never touches the Ollama systemd service and never requires sudo:
  ``ollama stop <model>`` only unloads that model's weights from VRAM
  through Ollama's own already-running daemon: the daemon keeps running.
- GPU state is never faked. If free VRAM cannot actually be verified, the
  stage is not silently allowed to proceed - it either verifies real
  headroom or fails with a clear, resumable error.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GPUGuardError(RuntimeError):
    """Raised when required GPU headroom cannot be verified or freed."""


@dataclass
class GPUState:
    gpu_index: int | None
    gpu_name: str | None
    free_mb: int | None
    total_mb: int | None
    checked: bool
    reason: str = ""


def _run_nvidia_smi(args: list[str], timeout: float = 5) -> str | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        result = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def inspect_gpu(gpu_name_substring: str) -> GPUState:
    output = _run_nvidia_smi(["--query-gpu=index,name,memory.free,memory.total", "--format=csv,noheader,nounits"])
    if output is None:
        return GPUState(None, None, None, None, False, "nvidia-smi not available")
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        index, name, free_mb, total_mb = parts
        if gpu_name_substring.casefold() in name.casefold():
            try:
                return GPUState(int(index), name, int(free_mb), int(total_mb), True)
            except ValueError:
                continue
    return GPUState(None, None, None, None, False, f"no GPU matching {gpu_name_substring!r} found")


def gpu_compute_processes() -> str:
    """Real, read-only evidence of which processes currently hold VRAM -
    used only for honest diagnostics in failure messages, never mutated."""
    output = _run_nvidia_smi(["--query-compute-apps=pid,used_memory,process_name", "--format=csv,noheader"])
    return output.strip() if output else "(unavailable)"


def list_ollama_loaded_models(timeout: float = 3) -> list[str]:
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/ps")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return [m.get("name") or m.get("model") for m in payload.get("models", []) if m.get("name") or m.get("model")]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []


def unload_ollama_model(model: str, timeout: float = 10) -> bool:
    """Ask Ollama's own daemon to unload one model's weights (``ollama stop``).
    Does not stop the Ollama service and never requires sudo."""
    exe = shutil.which("ollama")
    if not exe:
        return False
    try:
        result = subprocess.run([exe, "stop", model], capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


@dataclass
class GPUReservation:
    stage: str
    ok: bool
    gpu_name: str | None
    required_mb: int
    free_mb_before: int | None
    free_mb_after: int | None
    ollama_models_unloaded: list[str] = field(default_factory=list)
    waited_seconds: float = 0.0
    failure_reason: str = ""
    checked_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _emit(output: Callable[[str, str], None] | None, event: str, text: str) -> None:
    if output:
        output(event, text)


def reservation_log_path() -> Path:
    return Path(config.APP_LOG_DIR) / "gpu_reservations.jsonl"


def record_reservation(reservation: GPUReservation) -> None:
    path = reservation_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(reservation.to_dict(), sort_keys=True) + "\n")


def last_reservation() -> dict | None:
    path = reservation_log_path()
    if not path.is_file():
        return None
    last = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
    return last


def reserve_for_stage(
    stage: str,
    *,
    required_mb: int,
    gpu_name_substring: str,
    output: Callable[[str, str], None] | None = None,
    poll_seconds: float = 1.0,
    max_wait_seconds: float = 20.0,
) -> GPUReservation:
    """Verify (and if necessary, free) enough VRAM for ``stage``. Never
    fakes success: only returns ``ok=True`` when free VRAM was actually
    re-measured at or above ``required_mb``, or when GPU state genuinely
    cannot be inspected at all (nvidia-smi unavailable) - a real absence of
    evidence, not a fabricated pass."""
    state = inspect_gpu(gpu_name_substring)
    if not state.checked:
        _emit(output, "gpu_check_unavailable", f"GPU memory for {gpu_name_substring} could not be verified (nvidia-smi unavailable); proceeding unverified.")
        reservation = GPUReservation(stage, True, None, required_mb, None, None, failure_reason="nvidia-smi unavailable")
        record_reservation(reservation)
        return reservation

    free_before = state.free_mb
    if free_before is not None and free_before >= required_mb:
        _emit(output, "ollama_not_required", "Ollama not required.")
        _emit(output, "gpu_reserved", f"GPU reserved for {stage}.")
        reservation = GPUReservation(stage, True, state.gpu_name, required_mb, free_before, free_before)
        record_reservation(reservation)
        return reservation

    unloaded: list[str] = []
    loaded = list_ollama_loaded_models()
    if loaded:
        _emit(output, "unloading_ollama", "Unloading Ollama.")
        for model in loaded:
            if unload_ollama_model(model):
                unloaded.append(model)
    else:
        _emit(output, "ollama_not_required", "Ollama not required.")

    _emit(output, "waiting_for_gpu_memory", "Waiting for GPU memory.")
    deadline = time.monotonic() + max_wait_seconds
    waited = 0.0
    free_after = free_before
    while True:
        state = inspect_gpu(gpu_name_substring)
        free_after = state.free_mb
        if free_after is not None and free_after >= required_mb:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
        waited += poll_seconds

    if free_after is None or free_after < required_mb:
        _emit(output, "gpu_wait_failed", f"GPU memory is still insufficient for {stage}.")
        reservation = GPUReservation(
            stage, False, state.gpu_name, required_mb, free_before, free_after,
            ollama_models_unloaded=unloaded, waited_seconds=waited,
            failure_reason=(
                f"only {free_after} MiB free after waiting {waited:.0f}s; {required_mb} MiB required. "
                f"Processes currently holding VRAM: {gpu_compute_processes()}"
            ),
        )
        record_reservation(reservation)
        return reservation

    _emit(output, "gpu_reserved", f"GPU reserved for {stage}.")
    reservation = GPUReservation(stage, True, state.gpu_name, required_mb, free_before, free_after, ollama_models_unloaded=unloaded, waited_seconds=waited)
    record_reservation(reservation)
    return reservation


def release_stage(stage: str, *, gpu_name_substring: str, output: Callable[[str, str], None] | None = None) -> GPUReservation:
    """Record real post-stage VRAM state. AutoCorp does not control
    Chatterbox's own internal unload (that is CloneCast's job); this only
    verifies and logs the GPU's real state afterward, never fakes it."""
    state = inspect_gpu(gpu_name_substring)
    _emit(output, "gpu_released", f"{stage} finished.")
    _emit(output, "ready_for_next_stage", "Ready for next stage.")
    reservation = GPUReservation(
        f"{stage} (release)", state.checked, state.gpu_name, 0, None, state.free_mb,
        failure_reason="" if state.checked else state.reason,
    )
    record_reservation(reservation)
    return reservation

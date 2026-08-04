#!/usr/bin/env python3
"""
AutoCorp CLI - configuration
============================

Single source of truth for model, endpoint, timeouts, and resolved paths.
Everything is local; no keys, no cloud.
"""

import os

# --------------------------------------------------------------------------- #
# Ollama / model
# --------------------------------------------------------------------------- #
# Primary local model. The installed Ollama tag is "qwen2.5:14b" (which IS
# the 3.2B model); the bare "llama3.2:3b" tag is not pulled on this box.
MODEL = os.environ.get("AUTOCORP_MODEL", "qwen2.5:14b")
OLLAMA_URL = os.environ.get("AUTOCORP_OLLAMA_URL", "http://localhost:11434")

# Seconds to wait for a model response. First call of a session loads the model
# into memory and can be slow.
REQUEST_TIMEOUT = int(os.environ.get("AUTOCORP_TIMEOUT", "180"))

# How many times the Tester Brain may try to fix a failing build.
MAX_FIX_ATTEMPTS = int(os.environ.get("AUTOCORP_MAX_FIX_ATTEMPTS", "3"))

# Seconds before a command run inside a generated workspace is killed.
COMMAND_TIMEOUT = int(os.environ.get("AUTOCORP_COMMAND_TIMEOUT", "120"))

# --------------------------------------------------------------------------- #
# Optional Agent Watchdog integration (the WatchdogGate)
# --------------------------------------------------------------------------- #
# Agent Watchdog is a SEPARATE app. WatchdogGate loads it at runtime from this
# path as a library (no merging). If it can't be loaded, the gate falls back to
# the interactive ConfirmGate.
WATCHDOG_PATH = os.path.expanduser(
    os.environ.get("AUTOCORP_WATCHDOG_PATH", "~/agent_watchdog_brain")
)
# A reviewed command with risk >= this is blocked.
WATCHDOG_BLOCK_THRESHOLD = int(os.environ.get("AUTOCORP_WATCHDOG_BLOCK", "8"))
# Whether to use Watchdog's llama3.2 risk scoring on top of the deterministic
# pattern rules. Set AUTOCORP_WATCHDOG_AI=0 for fast, fully-offline rules only.
WATCHDOG_USE_AI = os.environ.get("AUTOCORP_WATCHDOG_AI", "1") != "0"

# --------------------------------------------------------------------------- #
# Reviewer Brain (Phase 8B) — deterministic static review
# --------------------------------------------------------------------------- #
# A function whose line span exceeds this is flagged as "large".
REVIEW_LARGE_FUNCTION_LINES = int(os.environ.get("AUTOCORP_REVIEW_LARGE_FUNC", "50"))
# Quality score starts at 100 and loses this many points per finding, by
# severity; the result is clamped to [0, 100].
REVIEW_SCORE_WEIGHTS = {"error": 15, "warning": 7, "info": 2}

# --------------------------------------------------------------------------- #
# Model Router (Phase 8C) — deterministic engine routing
# --------------------------------------------------------------------------- #
# Engine used when no routing rule matches (or a matched engine is unavailable).
ROUTE_DEFAULT_ENGINE = os.environ.get("AUTOCORP_ROUTE_DEFAULT", "local")

# --------------------------------------------------------------------------- #
# DeepSeek routing (Phase 8G) — config-only activation, OFF by default
# --------------------------------------------------------------------------- #
# An ordered, first-match-wins ruleset that reserves Claude for explicit
# architecture work and sends low-cost/simple builds to DeepSeek; everything else
# falls through to ROUTE_DEFAULT_ENGINE (the free local engine). Each rule names
# an already-registered engine; the router/engines/registry are UNCHANGED — this
# is purely a ruleset that the existing Model Router consumes.
DEEPSEEK_ROUTE_RULES = [
    {
        "name": "architecture-to-claude",
        "engine": "claude",
        "match": {
            "request_contains": [
                "architecture",
                "design system",
                "framework",
                "plugin system",
                "refactor",
                "migrate",
            ]
        },
    },
    {
        "name": "large-build-to-claude",
        "engine": "claude",
        "match": {
            "min_files": 8,
        },
    },
    {
        "name": "small-python-to-deepseek",
        "engine": "deepseek",
        "match": {
            "language": "python",
            "max_files": 5,
        },
    },
    {
        "name": "simple-types-to-deepseek",
        "engine": "deepseek",
        "match": {
            "project_type": ["cli", "script", "sqlite"],
        },
    },
]

# Opt-in toggle. DeepSeek routing only becomes the active ruleset when this is
# explicitly enabled; otherwise DEFAULT_ROUTE_RULES stays empty and routing
# behaves exactly as before (fall back to ROUTE_DEFAULT_ENGINE). Note routing is
# itself only consulted under `--engine auto`, so this is opt-in twice over.
DEEPSEEK_ROUTING_ENABLED = (
    os.environ.get("AUTOCORP_DEEPSEEK_ROUTING", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Default ruleset for `--engine auto`. Empty (current behaviour) unless DeepSeek
# routing is explicitly toggled on, in which case the DeepSeek ruleset applies.
# Each rule is a dict: {"name", "engine", "reason", "match": {...}}.
DEFAULT_ROUTE_RULES = DEEPSEEK_ROUTE_RULES if DEEPSEEK_ROUTING_ENABLED else []

# --------------------------------------------------------------------------- #
# Paths (all under the project root)
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
WORKSPACE_DIR = os.path.join(BASE_DIR, "workspace")
DB_PATH = os.path.join(DATA_DIR, "autocorp.db")

APP_NAME = "AutoCorp CLI"
APP_VERSION = "0.1.0"


def ensure_dirs() -> None:
    """Create the runtime directories if they do not exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(WORKSPACE_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# AutoCorp Chat App (Phase 1) — local desktop chat application
# --------------------------------------------------------------------------- #
# Bind address for the local FastAPI app. Loopback-only by default; external
# binding requires an explicit, non-default host to be passed at startup.
APP_HOST = os.environ.get("AUTOCORP_APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("AUTOCORP_APP_PORT", "8787"))

# Default CloneCast repository the guided episode workflow targets. Can be
# overridden per-request via Settings in the UI, but this is the supported
# default for the current machine.
CLONECAST_REPO_PATH = os.environ.get("AUTOCORP_CLONECAST_REPO", os.path.expanduser("~/clonecast"))

APP_SESSIONS_DIRNAME = "autocorp_app_sessions"
APP_UPLOADS_DIRNAME = "autocorp_app_uploads"

APP_UPLOAD_MAX_BYTES = int(os.environ.get("AUTOCORP_APP_UPLOAD_MAX_BYTES", str(200 * 1024 * 1024)))

APP_LOG_DIR = os.path.join(DATA_DIR, "autocorp_app_logs")
APP_PID_FILE = os.path.join(DATA_DIR, "autocorp_app.pid")
APP_DESKTOP_PID_FILE = os.path.join(DATA_DIR, "autocorp_desktop.pid")
APP_DESKTOP_FOCUS_FILE = os.path.join(DATA_DIR, "autocorp_desktop_focus.request")
APP_LOCK_FILE = os.path.join(DATA_DIR, "autocorp_app.lock")

# --------------------------------------------------------------------------- #
# GPU / Ollama coordination policy (permanent production rule)
# --------------------------------------------------------------------------- #
# Ollama is optional and disabled by default for the CloneCast production
# path: research and approved scripts are never written or rewritten by any
# local model (see app/gpu_guard.py and app/chat_controller.py). Before the
# real Chatterbox audio stage, AutoCorp verifies actual free VRAM and, only
# if necessary, asks Ollama's own API/CLI to unload its model - it never
# stops the Ollama service and never requires sudo.
GPU_GUARD_ENABLED = os.environ.get("AUTOCORP_GPU_GUARD", "1") != "0"
CHATTERBOX_GPU_NAME_SUBSTRING = os.environ.get("AUTOCORP_CHATTERBOX_GPU", "RTX 4060 Ti")
CHATTERBOX_REQUIRED_VRAM_MB = int(os.environ.get("AUTOCORP_CHATTERBOX_REQUIRED_MB", "8512"))
GPU_GUARD_MAX_WAIT_SECONDS = int(os.environ.get("AUTOCORP_GPU_GUARD_MAX_WAIT", "20"))

# Long-form Chatterbox renders can legitimately run far longer than the
# general CloneCast CLI timeout. AutoCorp sizes the speech-render deadline from
# the approved script and requested episode length, and emits periodic
# heartbeat progress while the CloneCast process is still alive.
CLONECAST_SPEECH_TIMEOUT_MIN_SECONDS = int(os.environ.get("AUTOCORP_SPEECH_TIMEOUT_MIN_SECONDS", "3600"))
CLONECAST_SPEECH_TIMEOUT_PER_TARGET_SECOND = float(os.environ.get("AUTOCORP_SPEECH_TIMEOUT_PER_TARGET_SECOND", "8"))
CLONECAST_SPEECH_TIMEOUT_PER_SCRIPT_WORD = float(os.environ.get("AUTOCORP_SPEECH_TIMEOUT_PER_SCRIPT_WORD", "4"))
CLONECAST_SPEECH_TIMEOUT_STARTUP_GRACE_SECONDS = int(
    os.environ.get("AUTOCORP_SPEECH_TIMEOUT_STARTUP_GRACE_SECONDS", "900")
)
CLONECAST_SPEECH_HEARTBEAT_SECONDS = int(os.environ.get("AUTOCORP_SPEECH_HEARTBEAT_SECONDS", "60"))
# A worker heartbeat proves the subprocess is still talking to us, not that
# it is making real progress: Chatterbox can get stuck emitting heartbeats
# forever inside one segment (a known TTS decode failure mode) without ever
# completing it. This is the max time real segment progress may stay flat
# before AutoCorp treats the render as stalled and stops it itself, rather
# than waiting on the much larger total-job timeout.
CLONECAST_SPEECH_STALL_SECONDS = int(os.environ.get("AUTOCORP_SPEECH_STALL_SECONDS", "240"))


def app_sessions_dir() -> str:
    return os.path.join(DATA_DIR, APP_SESSIONS_DIRNAME)


def app_uploads_dir() -> str:
    return os.path.join(DATA_DIR, APP_UPLOADS_DIRNAME)

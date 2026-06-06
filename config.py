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
# Primary local model. The installed Ollama tag is "llama3.2:latest" (which IS
# the 3.2B model); the bare "llama3.2:3b" tag is not pulled on this box.
MODEL = os.environ.get("AUTOCORP_MODEL", "llama3.2:latest")
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

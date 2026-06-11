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

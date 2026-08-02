#!/usr/bin/env bash
# Double-click / .desktop entry point for AutoCorp Chat.
#
# Resolves the repository root from this script's own location (not the
# caller's working directory), then runs the launcher through the
# repository's own virtual environment. Safe to run repeatedly: it will
# reuse an already-running server instead of starting a duplicate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON}" ]]; then
    echo "AutoCorp Error: virtual environment python not found at ${PYTHON}" >&2
    mkdir -p "${REPO_ROOT}/data/autocorp_app_logs"
    echo "$(date -Iseconds) STARTUP FAILED: missing venv python at ${PYTHON}" >> "${REPO_ROOT}/data/autocorp_app_logs/launcher.log"
    exit 1
fi

exec "${PYTHON}" -m app.launcher

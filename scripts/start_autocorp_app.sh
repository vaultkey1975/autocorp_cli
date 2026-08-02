#!/usr/bin/env bash
# Double-click / .desktop entry point for AutoCorp Chat.
#
# Resolves the repository root from this script's own location (not the
# caller's working directory), then runs the launcher through the
# repository's own virtual environment. Safe to run repeatedly: it will
# focus an already-running desktop window instead of starting a duplicate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"
LOG_DIR="${REPO_ROOT}/data/autocorp_app_logs"
DESKTOP_LOG="${LOG_DIR}/desktop-launch.log"
mkdir -p "${LOG_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
    echo "AutoCorp Error: virtual environment python not found at ${PYTHON}" >&2
    echo "$(date -Iseconds) STARTUP FAILED: missing venv python at ${PYTHON}" >> "${DESKTOP_LOG}"
    exit 1
fi

export QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-gpu-compositing"
export QT_QUICK_BACKEND=software

{
    echo "$(date -Iseconds) desktop launcher invoked"
    echo "$(date -Iseconds) QTWEBENGINE_CHROMIUM_FLAGS=${QTWEBENGINE_CHROMIUM_FLAGS}"
    echo "$(date -Iseconds) QT_QUICK_BACKEND=${QT_QUICK_BACKEND}"
} >> "${DESKTOP_LOG}" 2>&1

if command -v setsid >/dev/null 2>&1; then
    setsid "${PYTHON}" -m app.desktop_app >> "${DESKTOP_LOG}" 2>&1 &
else
    "${PYTHON}" -m app.desktop_app >> "${DESKTOP_LOG}" 2>&1 &
fi
echo "$(date -Iseconds) desktop wrapper launched with pid $!" >> "${DESKTOP_LOG}" 2>&1

#!/usr/bin/env bash
# Installs the AutoCorp desktop launcher for the current user.
#
# Idempotent: safe to run more than once. No sudo. Does not touch any other
# application's launcher. Points at the *installed repository path* this
# script lives in, resolved from the script's own location.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
START_SCRIPT="${REPO_ROOT}/scripts/start_autocorp_app.sh"
ICON_PATH="${REPO_ROOT}/desktop/autocorp.png"

chmod +x "${START_SCRIPT}"

APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "${APPLICATIONS_DIR}"
DESKTOP_FILE="${APPLICATIONS_DIR}/autocorp.desktop"

TMP_FILE="$(mktemp)"
cat > "${TMP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=AutoCorp
Comment=Local AutoCorp chat app for CloneCast episode production
Exec=${START_SCRIPT}
Icon=${ICON_PATH}
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=true
EOF
mv "${TMP_FILE}" "${DESKTOP_FILE}"
chmod +x "${DESKTOP_FILE}"

DESKTOP_DIR="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -z "${DESKTOP_DIR}" && -d "${HOME}/Desktop" ]]; then
    DESKTOP_DIR="${HOME}/Desktop"
fi
if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
    cp -f "${DESKTOP_FILE}" "${DESKTOP_DIR}/autocorp.desktop"
    chmod +x "${DESKTOP_DIR}/autocorp.desktop"
    gio set "${DESKTOP_DIR}/autocorp.desktop" metadata::trusted true >/dev/null 2>&1 || true
    DESKTOP_SHORTCUT="${DESKTOP_DIR}/autocorp.desktop"
else
    DESKTOP_SHORTCUT="(no Desktop directory found; skipped)"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
fi

LOG_DIR="${REPO_ROOT}/data/autocorp_app_logs"
mkdir -p "${LOG_DIR}"

echo "AutoCorp desktop launcher installed."
echo "Applications-menu entry: ${DESKTOP_FILE}"
echo "Desktop shortcut:        ${DESKTOP_SHORTCUT}"
echo "Icon path:               ${ICON_PATH}"
echo "Launcher script:         ${START_SCRIPT}"
echo "Startup log:             ${LOG_DIR}/launcher.log"
echo "PID file:                ${REPO_ROOT}/data/autocorp_app.pid"
echo "Local app address:       http://127.0.0.1:8787"

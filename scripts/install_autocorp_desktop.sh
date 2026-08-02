#!/usr/bin/env bash
# Installs/repairs the AutoCorp desktop launcher for the current user.
#
# Idempotent, no sudo. The repository's desktop/autocorp.desktop file is the
# source of truth; this script installs that exact entry into the app menu and
# onto the user's Desktop, then marks the Desktop copy trusted when GNOME/GIO
# supports that metadata.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${REPO_ROOT}/desktop/autocorp.desktop"
START_SCRIPT="${REPO_ROOT}/scripts/start_autocorp_app.sh"
ICON_PATH="${REPO_ROOT}/desktop/autocorp.png"
LOG_DIR="${REPO_ROOT}/data/autocorp_app_logs"

APPLICATIONS_DIR="${HOME}/.local/share/applications"
APPLICATIONS_FILE="${APPLICATIONS_DIR}/autocorp.desktop"
EXTRA_APPLICATIONS_DIR=""
if [[ -n "${XDG_DATA_HOME:-}" && "${XDG_DATA_HOME}/applications" != "${APPLICATIONS_DIR}" ]]; then
    EXTRA_APPLICATIONS_DIR="${XDG_DATA_HOME}/applications"
fi
DESKTOP_DIR="$(command -v xdg-user-dir >/dev/null 2>&1 && xdg-user-dir DESKTOP 2>/dev/null || true)"
if [[ -z "${DESKTOP_DIR}" && -d "${HOME}/Desktop" ]]; then
    DESKTOP_DIR="${HOME}/Desktop"
fi

if [[ ! -f "${TEMPLATE}" ]]; then
    echo "AutoCorp Error: missing desktop template at ${TEMPLATE}" >&2
    exit 1
fi
if [[ ! -f "${ICON_PATH}" ]]; then
    echo "AutoCorp Error: missing icon at ${ICON_PATH}" >&2
    exit 1
fi

mkdir -p "${APPLICATIONS_DIR}" "${LOG_DIR}"
chmod +x "${START_SCRIPT}"
chmod +x "${TEMPLATE}"

remove_stale_autocorp_entries() {
    local directory="$1"
    [[ -d "${directory}" ]] || return 0
    local path name exec
    shopt -s nullglob
    for path in "${directory}"/*.desktop; do
        [[ "$(basename "${path}")" != "autocorp.desktop" ]] || continue
        name="$(awk -F= '$1=="Name"{print $2; exit}' "${path}" 2>/dev/null || true)"
        exec="$(awk -F= '$1=="Exec"{print $2; exit}' "${path}" 2>/dev/null || true)"
        if [[ "${name}" == "AutoCorp" ]] || [[ "${exec}" == *"autocorp_cli"* ]]; then
            rm -f "${path}"
        fi
    done
    shopt -u nullglob
}

remove_stale_autocorp_entries "${APPLICATIONS_DIR}"
if [[ -n "${EXTRA_APPLICATIONS_DIR}" ]]; then
    remove_stale_autocorp_entries "${EXTRA_APPLICATIONS_DIR}"
    rm -f "${EXTRA_APPLICATIONS_DIR}/autocorp.desktop"
fi
if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
    remove_stale_autocorp_entries "${DESKTOP_DIR}"
fi

install -m 0755 "${TEMPLATE}" "${APPLICATIONS_FILE}"

DESKTOP_SHORTCUT="(no Desktop directory found; skipped)"
if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
    DESKTOP_SHORTCUT="${DESKTOP_DIR}/autocorp.desktop"
    install -m 0755 "${TEMPLATE}" "${DESKTOP_SHORTCUT}"
    if command -v gio >/dev/null 2>&1; then
        gio set "${DESKTOP_SHORTCUT}" metadata::trusted true >/dev/null 2>&1 || true
    fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    if [[ -n "${EXTRA_APPLICATIONS_DIR}" && -d "${EXTRA_APPLICATIONS_DIR}" ]]; then
        update-desktop-database "${EXTRA_APPLICATIONS_DIR}" >/dev/null 2>&1 || true
    fi
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q "${HOME}/.local/share/icons" >/dev/null 2>&1 || true
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi

echo "AutoCorp desktop launcher installed."
echo "Template:                ${TEMPLATE}"
echo "Applications-menu entry: ${APPLICATIONS_FILE}"
echo "Desktop shortcut:        ${DESKTOP_SHORTCUT}"
echo "Icon path:               ${ICON_PATH}"
echo "Launcher script:         ${START_SCRIPT}"
echo "Desktop launch log:      ${LOG_DIR}/desktop-launch.log"
echo "Startup log:             ${LOG_DIR}/launcher.log"
echo "PID file:                ${REPO_ROOT}/data/autocorp_app.pid"
echo "Local app address:       http://127.0.0.1:8787"

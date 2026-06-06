#!/usr/bin/env python3
"""
PySide6 Desktop template  (AutoCorp CLI - brains.templates)
==========================================================

Recognises desktop-GUI requests ("build a desktop calculator", "a GUI todo app",
"PySide6 inventory manager", ...) and produces a deterministic, structured plan
for a standard PySide6 application:

    project/
    ├── main.py              QApplication entry point
    ├── requirements.txt     PySide6
    └── ui/
        ├── __init__.py      package marker
        └── main_window.py   QMainWindow subclass

The plan defines a dependency-safe build_order (requirements + the ui package
before main.py, which imports `from ui.main_window import MainWindow`). The
actual code is still generated file-by-file by the Builder through the engine
abstraction - this template only fixes the project's shape and the per-file
instructions, so the result is always runnable.
"""

from brains.project_plan import sanitize_name

NAME = "pyside6_desktop"

# A request containing any of these (case-insensitive) selects this template.
KEYWORDS = ("pyside6", "pyside", "desktop", "gui", "window", "qt")

# Trigger / filler words stripped when deriving a project name from the request.
_STOP = {
    "build", "a", "an", "the", "me", "please", "create", "make", "app",
    "application", "desktop", "gui", "window", "pyside6", "pyside", "qt", "with",
}


def matches(request: str) -> bool:
    text = (request or "").lower()
    return any(keyword in text for keyword in KEYWORDS)


def _project_name(request: str) -> str:
    words = [w for w in (request or "").lower().split() if w.isalnum() and w not in _STOP]
    name = "_".join(words[:4]) if words else "desktop_app"
    name = sanitize_name(name)
    # Always make it clearly a desktop app.
    if "desktop" not in name and "app" not in name:
        name = f"{name}_app"
    return name or "desktop_app"


# The acceptance test EXECUTES the real entry point under the offscreen Qt
# platform: the app must start QApplication, construct + show MainWindow, run the
# event loop, and exit 0 - headless and without blocking. main.py self-quits under
# offscreen (see its generation instructions below); if anything is broken it
# crashes (or is killed by COMMAND_TIMEOUT) and the Fix Loop repairs it.
TEST_COMMAND = "QT_QPA_PLATFORM=offscreen python main.py"


def build_plan(request: str) -> dict:
    """Return a ProjectPlan-shaped dict for a PySide6 desktop app."""
    app_desc = (request or "a PySide6 desktop application").strip()

    files = [
        {
            "path": "requirements.txt",
            "purpose": (
                "Python dependencies for this PySide6 desktop app. "
                "Output EXACTLY one line containing: PySide6  (and nothing else)."
            ),
        },
        {
            "path": "ui/__init__.py",
            "purpose": (
                "Package marker so `ui` is an importable package. "
                "Output exactly one line: # ui package"
            ),
        },
        {
            "path": "ui/main_window.py",
            "purpose": (
                "Define a class named MainWindow that inherits QMainWindow "
                "(from PySide6.QtWidgets). In __init__: call super().__init__(); "
                "set the window title with setWindowTitle(); set the window size "
                "with resize(); create a central QWidget and pass it to "
                "setCentralWidget(); create a layout (e.g. QVBoxLayout) on that "
                "central widget; and add the starter UI controls (labels, "
                "buttons, inputs, lists, etc.) appropriate for this application: "
                f"{app_desc}. MainWindow() must construct with no arguments. Do "
                "NOT create a QApplication and do NOT call app.exec() in this file."
            ),
        },
        {
            "path": "main.py",
            "purpose": (
                "Application entry point, runnable with `python main.py`. It MUST "
                "start the Qt event loop and, under QT_QPA_PLATFORM=offscreen, exit "
                "cleanly with code 0 (no blocking). Begin with these imports, each "
                "on its own line: `import sys`; `import os`; `from "
                "PySide6.QtWidgets import QApplication`; `from PySide6.QtCore import "
                "QTimer`; `from ui.main_window import MainWindow`. Define main() "
                "that runs: app = QApplication(sys.argv); window = MainWindow(); "
                "window.show(); then, only for the offscreen platform, schedule an "
                "immediate quit so headless runs do not block - exactly: "
                "if os.environ.get(\"QT_QPA_PLATFORM\") == \"offscreen\": "
                "QTimer.singleShot(0, app.quit); finally sys.exit(app.exec()). "
                "Guard with if __name__ == \"__main__\": main()."
            ),
        },
    ]

    return {
        "project_name": _project_name(request),
        "project_type": "desktop",
        "language": "python",
        "summary": f"A PySide6 desktop application: {app_desc}",
        "files": files,
        # Dependency-safe: requirements + ui package before main.py (which imports
        # from ui.main_window). main.py is built last so it sees the real window.
        "build_order": [
            "requirements.txt",
            "ui/__init__.py",
            "ui/main_window.py",
            "main.py",
        ],
        "test_command": TEST_COMMAND,
        "success_criteria": [
            "main.py, ui/main_window.py and requirements.txt all exist",
            "requirements.txt contains PySide6",
            "MainWindow inherits QMainWindow and constructs with no arguments",
            "The application launches headless (offscreen) without error",
        ],
    }

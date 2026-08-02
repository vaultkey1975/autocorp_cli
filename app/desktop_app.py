"""AutoCorp local desktop wrapper."""

from __future__ import annotations

import os
import sys

import config
from app import desktop_lifecycle as lifecycle


def run() -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QIcon
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
    except ImportError as exc:
        lifecycle.log(f"STARTUP FAILED: PySide6 Qt WebEngine unavailable: {exc}")
        print(f"AutoCorp desktop wrapper requires PySide6 Qt WebEngine: {exc}", file=sys.stderr)
        return 1

    result = lifecycle.launch_server()
    if result.message.startswith("Focused existing"):
        return 0
    if not result.ready:
        print(result.message, file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    icon_path = os.path.join(config.BASE_DIR, "desktop", "autocorp.png")
    app.setWindowIcon(QIcon(icon_path))
    lifecycle.write_desktop_pid()

    class AutoCorpWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self._closing = False
            self.setWindowTitle("AutoCorp")
            self.resize(1280, 860)
            view = QWebEngineView(self)
            view.setUrl(QUrl(result.url))
            self.setCentralWidget(view)

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt API name
            if self._closing:
                event.accept()
                return
            lifecycle.log("close requested")
            active = lifecycle.active_generation_status().get("active", False)
            cancel = False
            if active:
                lifecycle.log("active job detected")
                box = QMessageBox(self)
                box.setWindowTitle("AutoCorp is generating")
                box.setText("An episode is actively generating.")
                box.setInformativeText("Keep AutoCorp open, or cancel the active job and close safely.")
                keep = box.addButton("Keep AutoCorp open", QMessageBox.ButtonRole.RejectRole)
                close = box.addButton("Cancel active job and close", QMessageBox.ButtonRole.AcceptRole)
                box.setDefaultButton(keep)
                box.exec()
                if box.clickedButton() is not close:
                    event.ignore()
                    return
                cancel = True
            lifecycle.shutdown_desktop(cancel_active=cancel, pid=result.pid)
            self._closing = True
            event.accept()

    window = AutoCorpWindow()
    window.show()
    lifecycle.log("desktop window opened")
    return app.exec()


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())

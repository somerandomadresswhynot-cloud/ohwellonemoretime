from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

try:
    from pdfjs_viewer import configure_global_stability
except Exception:  # pragma: no cover - optional runtime integration
    configure_global_stability = None

from study_app.persistence.database import Database
from study_app.persistence.repositories import HighlightRepo, OutlineRepo, ReviewRepo, SettingsRepo, SourceRepo
from study_app.pdf.pdf_service import PdfService
from study_app.ui.main_window import MainWindow
from study_app.ui.theme import DARK_QSS


def _configure_windows_webengine_fallback() -> None:
    if not sys.platform.startswith("win"):
        return

    # WebEngine may fail GPU context creation on some Windows machines; force a
    # software fallback and optionally enable diagnostics with:
    # QT_LOGGING_RULES="qt.webenginecontext.debug=true;qt.webenginecore.debug=true"
    QApplication.setAttribute(Qt.AA_UseSoftwareOpenGL)
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")


def run() -> None:
    _configure_windows_webengine_fallback()

    if configure_global_stability:
        configure_global_stability()

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)

    db = Database("study_app.db")
    source_repo = SourceRepo(db)
    outline_repo = OutlineRepo(db)
    review_repo = ReviewRepo(db)
    settings_repo = SettingsRepo(db)
    highlight_repo = HighlightRepo(db)
    pdf_service = PdfService()

    w = MainWindow(source_repo, outline_repo, review_repo, settings_repo, highlight_repo, pdf_service)
    w.show()
    app.aboutToQuit.connect(db.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    run()

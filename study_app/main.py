from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
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


def run() -> None:
    if configure_global_stability:
        configure_global_stability()

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app_font = QFont("Segoe UI", 10)
    app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(app_font)
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

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
except Exception:  # runtime guard if QtPdf missing
    QPdfDocument = None
    QPdfView = None


class PersistentPdfViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.current_path = ""
        self._doc = None
        self._view = None
        self._label = QLabel("PDF view unavailable (QtPdf missing).")
        self._label.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            self._doc = QPdfDocument(self)
            self._view = QPdfView(self)
            self._view.setDocument(self._doc)
            layout.addWidget(self._view)
        else:
            layout.addWidget(self._label)

    def load_if_needed(self, path: str) -> None:
        if not path or not Path(path).exists() or not self._doc:
            return
        if path != self.current_path:
            self._doc.load(path)
            self.current_path = path

    def set_page(self, page: int) -> None:
        if self._view:
            nav = self._view.pageNavigator()
            nav.jump(max(0, page - 1), 0, 0)

    def set_zoom(self, factor: float) -> None:
        if self._view:
            self._view.setZoomFactor(factor)

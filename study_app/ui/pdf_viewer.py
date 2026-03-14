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
        self._last_page = 1
        self._last_location = (0.0, 0.0)

        layout = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            self._doc = QPdfDocument(self)
            self._view = QPdfView(self)
            self._view.setDocument(self._doc)
            self.set_fit_mode()
            try:
                self._view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
            except Exception:
                pass
            layout.addWidget(self._view)
        else:
            layout.addWidget(self._label)

    def _on_page_changed(self, page_zero: int) -> None:
        self._last_page = max(1, int(page_zero) + 1)

    def load_if_needed(self, path: str) -> None:
        if not path or not Path(path).exists() or not self._doc:
            return
        if path != self.current_path:
            self._doc.load(path)
            self.current_path = path
            self._last_page = 1
            self._last_location = (0.0, 0.0)
            self.set_fit_mode()

    def set_fit_mode(self) -> None:
        if self._view:
            try:
                self._view.setZoomMode(QPdfView.ZoomMode.FitInView)
            except Exception:
                pass

    def set_page(self, page: int, location: tuple[float, float] | None = None) -> None:
        if self._view:
            nav = self._view.pageNavigator()
            x, y = location if location else (0, 0)
            nav.jump(max(0, page - 1), x, y)
            self._last_page = max(1, page)
            self._last_location = (float(x), float(y))

    def set_zoom(self, factor: float) -> None:
        if self._view:
            self._view.setZoomMode(QPdfView.ZoomMode.Custom)
            self._view.setZoomFactor(factor)

    def view_state(self) -> dict:
        page = self._last_page
        loc = self._last_location
        if self._view:
            nav = self._view.pageNavigator()
            try:
                page = int(nav.currentPage()) + 1
            except Exception:
                pass
            try:
                p = nav.currentLocation()
                loc = (float(p.x()), float(p.y()))
            except Exception:
                pass
        return {"page": page, "location": loc}

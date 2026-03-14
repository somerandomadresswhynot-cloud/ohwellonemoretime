from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

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

        root = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            self._doc = QPdfDocument(self)
            self._view = QPdfView(self)
            self._view.setDocument(self._doc)
            self._apply_default_view_mode()
            try:
                self._view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
            except Exception:
                pass
            root.addWidget(self._view)

            controls = QHBoxLayout()
            self.zoom_out_btn = QPushButton("-")
            self.zoom_in_btn = QPushButton("+")
            self.zoom_pct = QSpinBox()
            self.zoom_pct.setRange(25, 400)
            self.zoom_pct.setValue(100)
            self.fit_width_btn = QPushButton("Fit Width")
            self.fit_page_btn = QPushButton("Fit Page")
            self.fullscreen_btn = QPushButton("Full Screen")

            self.zoom_out_btn.clicked.connect(lambda: self.set_zoom(max(0.25, self.zoom_factor() - 0.1)))
            self.zoom_in_btn.clicked.connect(lambda: self.set_zoom(min(4.0, self.zoom_factor() + 0.1)))
            self.zoom_pct.valueChanged.connect(lambda v: self.set_zoom(v / 100.0))
            self.fit_width_btn.clicked.connect(self.set_fit_mode)
            self.fit_page_btn.clicked.connect(self.set_fit_page_mode)
            self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

            for w in [QLabel("Scale"), self.zoom_out_btn, self.zoom_pct, self.zoom_in_btn, self.fit_width_btn, self.fit_page_btn, self.fullscreen_btn]:
                controls.addWidget(w)
            controls.addStretch()
            root.addLayout(controls)
        else:
            root.addWidget(self._label)

    def zoom_factor(self) -> float:
        if not self._view:
            return 1.0
        try:
            return float(self._view.zoomFactor())
        except Exception:
            return 1.0

    def _sync_zoom_spin(self) -> None:
        if hasattr(self, "zoom_pct"):
            self.zoom_pct.blockSignals(True)
            self.zoom_pct.setValue(int(self.zoom_factor() * 100))
            self.zoom_pct.blockSignals(False)

    def _on_page_changed(self, page_zero: int) -> None:
        self._last_page = max(1, int(page_zero) + 1)

    def _apply_default_view_mode(self) -> None:
        if not self._view:
            return
        self.set_multi_page_mode()
        self.set_fit_mode()

    def set_multi_page_mode(self) -> None:
        if not self._view:
            return
        try:
            self._view.setPageMode(QPdfView.PageMode.MultiPage)
        except Exception:
            pass

    def set_single_page_mode(self) -> None:
        if not self._view:
            return
        try:
            self._view.setPageMode(QPdfView.PageMode.SinglePage)
        except Exception:
            pass

    def load_if_needed(self, path: str) -> None:
        if not path or not Path(path).exists() or not self._doc:
            return
        if path != self.current_path:
            self._doc.load(path)
            self.current_path = path
            self._last_page = 1
            self._last_location = (0.0, 0.0)
            self._apply_default_view_mode()

    def set_fit_mode(self) -> None:
        if self._view:
            try:
                self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            except Exception:
                try:
                    self._view.setZoomMode(QPdfView.ZoomMode.FitInView)
                except Exception:
                    pass
            self._sync_zoom_spin()

    def set_fit_page_mode(self) -> None:
        if self._view:
            try:
                self._view.setZoomMode(QPdfView.ZoomMode.FitInView)
            except Exception:
                pass
            self._sync_zoom_spin()

    def set_page(self, page: int, location: tuple[float, float] | None = None) -> None:
        if not self._view:
            return
        nav = self._view.pageNavigator()
        x, y = location if location else (0.0, 0.0)
        try:
            nav.jump(max(0, page - 1), QPointF(float(x), float(y)), self._view.zoomFactor())
        except Exception:
            try:
                nav.jump(max(0, page - 1), QPointF(float(x), float(y)), 0.0)
            except Exception:
                pass
        self._last_page = max(1, int(page))
        self._last_location = (float(x), float(y))

    def set_zoom(self, factor: float) -> None:
        if self._view:
            self._view.setZoomMode(QPdfView.ZoomMode.Custom)
            self._view.setZoomFactor(float(factor))
            self._sync_zoom_spin()

    def toggle_fullscreen(self) -> None:
        window = self.window()
        if not window:
            return
        if window.isFullScreen():
            window.showNormal()
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Full Screen")
        else:
            window.showFullScreen()
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Exit Full Screen")

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

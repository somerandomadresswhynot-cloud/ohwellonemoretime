from __future__ import annotations

from collections import OrderedDict
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
        self._view = None
        self._label = QLabel("PDF view unavailable (QtPdf missing).")
        self._label.setAlignment(Qt.AlignCenter)
        self._last_page = 1
        self._last_location = (0.0, 0.0)

        self._doc_cache: OrderedDict[str, QPdfDocument] = OrderedDict()
        self._cache_limit = 8
        self._fullscreen_host = None
        self._view_original_parent = None
        self._view_original_layout = None

        root = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            self._view = QPdfView(self)
            self._apply_default_view_mode()
            root.addWidget(self._view)

            controls = QHBoxLayout()
            self.zoom_out_btn = QPushButton("-")
            self.zoom_in_btn = QPushButton("+")
            self.zoom_pct = QSpinBox()
            self.zoom_pct.setRange(25, 400)
            self.zoom_pct.setValue(100)
            self.fit_width_btn = QPushButton("Fit Width")
            self.fit_page_btn = QPushButton("Fit Page")
            self.fullscreen_btn = QPushButton("Viewer Full Screen")

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

    def _attach_page_changed(self, doc: QPdfDocument) -> None:
        try:
            self._view.pageNavigator().currentPageChanged.disconnect(self._on_page_changed)
        except Exception:
            pass
        try:
            self._view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
        except Exception:
            pass

    def _cache_doc(self, path: str, doc: QPdfDocument) -> None:
        self._doc_cache[path] = doc
        self._doc_cache.move_to_end(path)
        while len(self._doc_cache) > self._cache_limit:
            _, dropped = self._doc_cache.popitem(last=False)
            try:
                dropped.deleteLater()
            except Exception:
                pass

    def prime_path(self, path: str) -> None:
        if not path or not Path(path).exists() or not QPdfDocument:
            return
        if path in self._doc_cache:
            self._doc_cache.move_to_end(path)
            return
        doc = QPdfDocument(self)
        # QPdfDocument load/error enums differ across Qt versions; cache doc directly after load call.
        doc.load(path)
        self._cache_doc(path, doc)

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
        if not path or not Path(path).exists() or not self._view or not QPdfDocument:
            return
        if path == self.current_path:
            return

        if path in self._doc_cache:
            doc = self._doc_cache[path]
            self._doc_cache.move_to_end(path)
        else:
            doc = QPdfDocument(self)
            doc.load(path)
            self._cache_doc(path, doc)

        self._view.setDocument(doc)
        self._attach_page_changed(doc)
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
        if not self._view:
            return
        if self._fullscreen_host is None:
            self._view_original_parent = self._view.parentWidget()
            self._view_original_layout = self.layout()
            if self._view_original_layout:
                self._view_original_layout.removeWidget(self._view)
            self._fullscreen_host = QWidget()
            self._fullscreen_host.setWindowTitle("PDF Viewer")
            self._fullscreen_host.setWindowFlag(Qt.Window)
            lay = QVBoxLayout(self._fullscreen_host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self._view)
            self._fullscreen_host.showFullScreen()
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Exit Viewer Full Screen")
        else:
            host = self._fullscreen_host
            host.layout().removeWidget(self._view)
            if self._view_original_layout:
                self._view_original_layout.insertWidget(0, self._view)
            self._view.setParent(self._view_original_parent)
            host.close()
            host.deleteLater()
            self._fullscreen_host = None
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Viewer Full Screen")

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

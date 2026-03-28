from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

try:
    from pdfjs_viewer import PDFViewerWidget
except Exception:  # pragma: no cover - dependency may be unavailable in dev env
    PDFViewerWidget = None


class EmbeddedPdfViewer(QWidget):
    """Thin app-local wrapper over `pdfjs-viewer-pyside6`'s `PDFViewerWidget`."""

    def __init__(self) -> None:
        super().__init__()
        self.current_path = ""
        self._viewer = None
        self._last_page = 1
        self._default_zoom_pct = self._compute_default_zoom_pct()
        self._last_zoom: int | str = self._default_zoom_pct
        self._selection_menu_handler: Callable | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        if PDFViewerWidget is None:
            fallback = QLabel("PDF viewer unavailable: install `pdfjs-viewer-pyside6`.")
            fallback.setAlignment(Qt.AlignCenter)
            root.addWidget(fallback)
            self._fallback = fallback
            return

        self._viewer = PDFViewerWidget(preset="simple")
        try:
            self._viewer.page_changed.connect(self._on_page_changed)
        except Exception:
            pass
        self._viewer.setFocusPolicy(Qt.StrongFocus)
        self._viewer.setContextMenuPolicy(Qt.CustomContextMenu)
        self._viewer.customContextMenuRequested.connect(self._on_context_menu)

        root.addWidget(self._viewer, 1)

        controls = QHBoxLayout()
        self.zoom_out_btn = QPushButton("-")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_pct = QSpinBox()
        self.zoom_pct.setRange(25, 400)
        self.zoom_pct.setValue(self._default_zoom_pct)
        self.fit_width_btn = QPushButton("Fit Width")
        self.fit_page_btn = QPushButton("Fit Page")

        self.zoom_out_btn.clicked.connect(lambda: self.set_zoom(max(0.25, self.zoom_factor() - 0.1)))
        self.zoom_in_btn.clicked.connect(lambda: self.set_zoom(min(4.0, self.zoom_factor() + 0.1)))
        self.zoom_pct.valueChanged.connect(lambda v: self.set_zoom(v / 100.0))
        self.fit_width_btn.clicked.connect(self.set_fit_mode)
        self.fit_page_btn.clicked.connect(self.set_fit_page_mode)

        for w in [QLabel("Scale"), self.zoom_out_btn, self.zoom_pct, self.zoom_in_btn, self.fit_width_btn, self.fit_page_btn]:
            controls.addWidget(w)
        controls.addStretch()
        root.addLayout(controls)

    def _compute_default_zoom_pct(self) -> int:
        screen = QApplication.primaryScreen()
        if not screen:
            return 125
        try:
            dpr = float(screen.devicePixelRatio())
        except Exception:
            dpr = 1.0
        if dpr >= 1.5:
            return 115
        return 125

    def _on_context_menu(self, _pos) -> None:
        if not self._selection_menu_handler:
            return
        self._selection_menu_handler(QCursor.pos(), self.selected_text(), int(self.view_state().get("page", 1)))

    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler

    def _on_page_changed(self, current: int, _total: int) -> None:
        self._last_page = max(1, int(current))

    def selected_text(self) -> str:
        try:
            return (QApplication.clipboard().text() or "").strip()
        except Exception:
            return ""

    def prime_path(self, path: str) -> None:
        # Intentionally no-op for PDF.js viewer backend.
        _ = path

    def zoom_factor(self) -> float:
        try:
            return float(self.zoom_pct.value()) / 100.0
        except Exception:
            return 1.0

    def _reload(self, *, page: int | None = None, zoom: int | str | None = None) -> None:
        if not self._viewer or not self.current_path:
            return
        load_page = page if page is not None else self._last_page
        load_zoom = zoom if zoom is not None else self._last_zoom
        self._viewer.load_pdf(self.current_path, page=max(1, int(load_page)), zoom=load_zoom)

    def set_multi_page_mode(self) -> None:
        # PDF.js viewer handles continuous scrolling natively.
        return

    def set_single_page_mode(self) -> None:
        # No explicit single-page API needed for current integration.
        return

    def load_if_needed(self, path: str) -> None:
        if not self._viewer:
            return
        if not path or not Path(path).exists():
            return
        if path == self.current_path:
            return
        self.current_path = path
        self._last_page = 1
        self._last_zoom = self._default_zoom_pct
        self._viewer.load_pdf(path, page=1, zoom=self._last_zoom)

    def set_fit_mode(self) -> None:
        self._last_zoom = "page-width"
        self._reload(zoom=self._last_zoom)

    def set_fit_page_mode(self) -> None:
        self._last_zoom = "page-fit"
        self._reload(zoom=self._last_zoom)

    def set_page(self, page: int, location=None) -> None:
        _ = location
        self._last_page = max(1, int(page))
        if self._viewer:
            self._viewer.goto_page(self._last_page)

    def set_zoom(self, factor: float) -> None:
        zoom_pct = max(25, min(400, int(round(float(factor) * 100))))
        self.zoom_pct.blockSignals(True)
        self.zoom_pct.setValue(zoom_pct)
        self.zoom_pct.blockSignals(False)
        self._last_zoom = zoom_pct
        self._reload(zoom=zoom_pct)

    def view_state(self) -> dict:
        page = self._last_page
        if self._viewer:
            try:
                page = max(1, int(self._viewer.get_current_page()))
            except Exception:
                pass
        return {"page": page, "location": (0.0, 0.0)}

    # Legacy methods kept as no-ops so higher-level screens can ignore old overlay paths.
    def set_annotation_tool(self, tool: str) -> None:
        _ = tool

    def set_area_created_handler(self, handler) -> None:
        _ = handler

    def set_highlight_hit_handler(self, handler) -> None:
        _ = handler

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        _ = highlights

    def toggle_fullscreen(self) -> None:
        if not self._viewer:
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()


# Backward-compatible name for callsites still importing PersistentPdfViewer.
PersistentPdfViewer = EmbeddedPdfViewer

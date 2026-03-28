from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QClipboard, QCursor, QKeySequence
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
        self._last_zoom: int | str = "page-width"
        self._pending_page: int | None = None
        self._pending_page_attempts = 0
        self._selection_menu_handler: Callable | None = None
        self._last_copied_text = ""
        self._copy_from_viewer_pending = False
        self._clipboard_sync_in_progress = False

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
        self._viewer.installEventFilter(self)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._on_clipboard_changed)

        root.addWidget(self._viewer, 1)

        controls = QHBoxLayout()
        self.zoom_out_btn = QPushButton("-")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_pct = QSpinBox()
        self.zoom_pct.setRange(25, 400)
        self.zoom_pct.setValue(100)
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
            clipboard_text = QApplication.clipboard().text() or ""
            if clipboard_text:
                self._last_copied_text = clipboard_text
            return (clipboard_text or self._last_copied_text).strip()
        except Exception:
            return ""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming convention
        if watched is self._viewer and event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.Copy):
                self._copy_from_viewer_pending = True
                QTimer.singleShot(1200, self._clear_copy_pending_flag)
        return super().eventFilter(watched, event)

    def _clear_copy_pending_flag(self) -> None:
        self._copy_from_viewer_pending = False

    def _on_clipboard_changed(self) -> None:
        if not self._viewer or self._clipboard_sync_in_progress:
            return
        if not (self._viewer.hasFocus() or self._copy_from_viewer_pending):
            return
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        text = clipboard.text() or ""
        if not text:
            return
        self._last_copied_text = text
        # Materialize copied text immediately in app-owned memory so very long selections
        # are stable even if the embedded viewer virtualizes text layers while scrolling.
        try:
            self._clipboard_sync_in_progress = True
            clipboard.setText(text, QClipboard.Clipboard)
            try:
                clipboard.setText(text, QClipboard.Selection)
            except Exception:
                pass
        finally:
            self._clipboard_sync_in_progress = False

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

    def load_if_needed(self, path: str) -> bool:
        if not self._viewer:
            return False
        if not path or not Path(path).exists():
            return False
        if path == self.current_path:
            return False
        self.current_path = path
        self._last_page = 1
        self._last_zoom = "page-width"
        self._pending_page = None
        self._pending_page_attempts = 0
        self._viewer.load_pdf(path, page=1, zoom=self._last_zoom)
        return True

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
            self._pending_page = self._last_page
            self._pending_page_attempts = 0
            self._viewer.goto_page(self._last_page)
            # Some backends ignore immediate goto while a fresh PDF load is settling.
            # Retry for a short window until the target page is confirmed.
            QTimer.singleShot(0, self._apply_pending_page)
            QTimer.singleShot(90, self._apply_pending_page)

    def _apply_pending_page(self) -> None:
        if not self._viewer or self._pending_page is None:
            return
        page = max(1, int(self._pending_page))
        self._viewer.goto_page(page)
        current_page = None
        try:
            current_page = int(self._viewer.get_current_page())
        except Exception:
            current_page = None
        if current_page == page:
            self._pending_page = None
            self._pending_page_attempts = 0
            return
        self._pending_page_attempts += 1
        if self._pending_page_attempts >= 12:
            self._pending_page = None
            self._pending_page_attempts = 0
            return
        QTimer.singleShot(90, self._apply_pending_page)

    def set_zoom(self, factor: float) -> None:
        zoom_pct = max(25, min(400, int(round(float(factor) * 100))))
        if isinstance(self._last_zoom, int) and int(self._last_zoom) == zoom_pct:
            self.zoom_pct.blockSignals(True)
            self.zoom_pct.setValue(zoom_pct)
            self.zoom_pct.blockSignals(False)
            return
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

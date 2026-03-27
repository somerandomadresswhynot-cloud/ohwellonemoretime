from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView


class SimplePdfViewer(QWidget):
    """Minimal embedded Chromium PDF viewer."""

    def __init__(self):
        super().__init__()
        self.current_path = ""
        self._last_page = 1
        self._last_location = (0.0, 0.0)

        root = QVBoxLayout(self)
        toolbar = QHBoxLayout()

        self.reload_btn = QPushButton("Reload")
        self.open_external_btn = QPushButton("Open Externally")
        self.reload_btn.clicked.connect(self.reload_pdf)
        self.open_external_btn.clicked.connect(self.open_externally)
        toolbar.addWidget(self.reload_btn)
        toolbar.addWidget(self.open_external_btn)
        toolbar.addStretch()

        self._status = QLabel("")
        self._status.setStyleSheet("color:#9aa7b2;")

        self._view = QWebEngineView(self)
        self._view.setContextMenuPolicy(Qt.DefaultContextMenu)
        self._view.setFocusPolicy(Qt.StrongFocus)
        self._view.settings().setAttribute(QWebEngineSettings.PluginsEnabled, True)
        self._view.settings().setAttribute(QWebEngineSettings.PdfViewerEnabled, True)
        self._view.loadStarted.connect(lambda: self._status.setText("Loading PDF…"))
        self._view.loadFinished.connect(self._on_load_finished)

        root.addLayout(toolbar)
        root.addWidget(self._view, 1)
        root.addWidget(self._status)

    def _pdf_url(self, file_path: str, page: int | None = None) -> QUrl:
        url = QUrl.fromLocalFile(str(Path(file_path).resolve()))
        if page and page > 1:
            url.setFragment(f"page={int(page)}")
        return url

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._status.setText("")
            self._view.setFocus(Qt.OtherFocusReason)
            return
        self._status.setText("Unable to load PDF.")

    def open_pdf(self, file_path: str) -> None:
        if not file_path or not Path(file_path).exists():
            self._status.setText("PDF not found.")
            return
        self.current_path = str(file_path)
        self._last_page = 1
        self._last_location = (0.0, 0.0)
        self._view.setUrl(self._pdf_url(file_path))

    def load_if_needed(self, path: str) -> None:
        if path and path != self.current_path:
            self.open_pdf(path)

    def reload_pdf(self) -> None:
        self._view.reload()

    def open_externally(self) -> None:
        if self.current_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.current_path))

    def go_to_page(self, page_number: int) -> None:
        if not self.current_path:
            return
        page = max(1, int(page_number))
        self._last_page = page
        self._view.setUrl(self._pdf_url(self.current_path, page=page))

    def set_page(self, page: int, location: tuple[float, float] | None = None) -> None:
        self._last_location = location if location else (0.0, 0.0)
        self.go_to_page(page)

    def zoom_in(self) -> None:
        self._view.setZoomFactor(min(5.0, self._view.zoomFactor() + 0.1))

    def zoom_out(self) -> None:
        self._view.setZoomFactor(max(0.25, self._view.zoomFactor() - 0.1))

    def reset_zoom(self) -> None:
        self._view.setZoomFactor(1.0)

    def set_zoom(self, factor: float) -> None:
        self._view.setZoomFactor(max(0.25, min(5.0, float(factor))))

    def zoom_factor(self) -> float:
        return float(self._view.zoomFactor())

    def set_fit_mode(self) -> None:
        self.reset_zoom()

    def set_fit_page_mode(self) -> None:
        self.reset_zoom()

    def set_multi_page_mode(self) -> None:
        return

    def set_single_page_mode(self) -> None:
        return

    def prime_path(self, path: str) -> None:
        return

    def view_state(self) -> dict:
        try:
            frag = self._view.url().fragment()
            if frag:
                params = parse_qs(frag, keep_blank_values=True)
                if "page" in params and params["page"]:
                    self._last_page = max(1, int(params["page"][0]))
        except Exception:
            pass
        return {"page": int(self._last_page), "location": self._last_location}

    def selected_text(self) -> str:
        return ""

    def set_selection_menu_handler(self, _handler) -> None:
        return

    def set_area_created_handler(self, _handler) -> None:
        return

    def set_highlight_hit_handler(self, _handler) -> None:
        return

    def set_overlay_highlights(self, _highlights: list[dict]) -> None:
        return

    def set_annotation_tool(self, _tool: str) -> None:
        return

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

try:
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:
    QWebChannel = None
    QWebEngineView = None


def normalize_annotation_rect(rect: dict) -> dict:
    out = {
        "x": float(rect.get("x", 0.0)),
        "y": float(rect.get("y", 0.0)),
        "width": float(rect.get("width", rect.get("w", 0.0))),
        "height": float(rect.get("height", rect.get("h", 0.0))),
    }
    if ("w" in rect or "h" in rect) and all(0.0 <= float(out[k]) <= 1.0 for k in ("x", "y", "width", "height")):
        out["coord_space"] = "normalized"
    else:
        out["coord_space"] = str(rect.get("coord_space", "page"))
    return out


def project_page_rect_to_viewport(rect: dict, page_size: tuple[float, float], viewport_size: tuple[float, float]) -> dict:
    px_w = max(1.0, float(page_size[0]))
    px_h = max(1.0, float(page_size[1]))
    vw = max(1.0, float(viewport_size[0]))
    vh = max(1.0, float(viewport_size[1]))
    scale_x = vw / px_w
    scale_y = vh / px_h
    r = normalize_annotation_rect(rect)
    if r["coord_space"] == "normalized":
        return {
            "left": r["x"] * vw,
            "top": r["y"] * vh,
            "width": r["width"] * vw,
            "height": r["height"] * vh,
        }
    return {
        "left": r["x"] * scale_x,
        "top": (px_h - (r["y"] + r["height"])) * scale_y,
        "width": r["width"] * scale_x,
        "height": r["height"] * scale_y,
    }


class PdfJsBridge(QObject):
    selection_changed = Signal(str, dict)
    annotation_created = Signal(dict)
    annotation_deleted = Signal(str)
    page_changed = Signal(int)
    viewer_ready = Signal(dict)

    @Slot(str, str)
    def emit_selection_changed(self, selected_text: str, page_info_json: str = "{}") -> None:
        info = self._loads(page_info_json)
        self.selection_changed.emit(selected_text or "", info)

    @Slot(str)
    def emit_annotation_created(self, annotation_payload_json: str) -> None:
        self.annotation_created.emit(self._loads(annotation_payload_json))

    @Slot(str)
    def emit_annotation_deleted(self, annotation_id: str) -> None:
        self.annotation_deleted.emit(str(annotation_id or ""))

    @Slot(int)
    def emit_page_changed(self, page_number: int) -> None:
        self.page_changed.emit(max(1, int(page_number or 1)))

    @Slot(str)
    def emit_viewer_ready(self, capabilities_json: str = "{}") -> None:
        self.viewer_ready.emit(self._loads(capabilities_json))

    def _loads(self, value: str) -> dict:
        try:
            data = json.loads(value or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


class PdfJsViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.current_path = ""
        self._last_page = 1
        self._selection_text = ""
        self._selection_page_info: dict = {}
        self._selection_menu_handler = None
        self._area_created_handler = None
        self._highlight_hit_handler = None
        self._tool = "select_text"
        self._pending_annotations: list[dict] = []

        root = QVBoxLayout(self)
        if not QWebEngineView or not QWebChannel:
            label = QLabel("PDF.js viewer unavailable (QtWebEngine missing).")
            label.setAlignment(Qt.AlignCenter)
            root.addWidget(label)
            self._web = None
            return

        self._web = QWebEngineView(self)
        self._bridge = PdfJsBridge()
        self._channel = QWebChannel(self._web.page())
        self._channel.registerObject("pyBridge", self._bridge)
        self._web.page().setWebChannel(self._channel)
        self._web.setContextMenuPolicy(Qt.CustomContextMenu)
        self._web.customContextMenuRequested.connect(self._on_context_menu)

        self._bridge.selection_changed.connect(self._on_selection_changed)
        self._bridge.annotation_created.connect(self._on_annotation_created)
        self._bridge.annotation_deleted.connect(self._on_annotation_deleted)
        self._bridge.page_changed.connect(self._on_page_changed)
        self._bridge.viewer_ready.connect(self._on_viewer_ready)

        root.addWidget(self._web, 1)

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
        self.fit_width_btn.clicked.connect(lambda: self.set_zoom("page-width"))
        self.fit_page_btn.clicked.connect(lambda: self.set_zoom("page-fit"))

        for w in [QLabel("Scale"), self.zoom_out_btn, self.zoom_pct, self.zoom_in_btn, self.fit_width_btn, self.fit_page_btn]:
            controls.addWidget(w)
        controls.addStretch(1)
        root.addLayout(controls)

        host_url = QUrl.fromLocalFile(str((Path(__file__).parent / "web" / "pdfjs_host.html").resolve()))
        self._web.load(host_url)

    # compatibility API used by app
    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler

    def set_area_created_handler(self, handler) -> None:
        self._area_created_handler = handler

    def set_highlight_hit_handler(self, handler) -> None:
        self._highlight_hit_handler = handler

    def selected_text(self) -> str:
        return (self._selection_text or "").strip()

    def view_state(self) -> dict:
        return {"page": self._last_page, "location": (0.0, 0.0)}

    def zoom_factor(self) -> float:
        return float(self.zoom_pct.value()) / 100.0

    def set_zoom(self, mode_or_value) -> None:
        if isinstance(mode_or_value, (int, float)):
            v = max(0.25, min(4.0, float(mode_or_value)))
            self.zoom_pct.blockSignals(True)
            self.zoom_pct.setValue(int(round(v * 100)))
            self.zoom_pct.blockSignals(False)
            self._js_call("setZoom", v)
            return
        mode = str(mode_or_value or "page-width")
        self._js_call("setZoom", mode)

    def set_fit_mode(self) -> None:
        self.set_zoom("page-width")

    def set_fit_page_mode(self) -> None:
        self.set_zoom("page-fit")

    def set_multi_page_mode(self) -> None:
        self._js_call("setPageMode", "vertical")

    def set_single_page_mode(self) -> None:
        self._js_call("setPageMode", "single")

    def set_page(self, page: int, location: tuple[float, float] | None = None) -> None:
        _ = location
        self.go_to_page(page)


    def prime_path(self, path: str) -> None:
        # Compatibility with the previous QtPdf viewer API.
        # QWebEngine/PDF.js has no document cache hook here, so we just validate input.
        if not path:
            return
        try:
            _ = Path(path).exists()
        except Exception:
            return

    def load_if_needed(self, path: str) -> None:
        if not path:
            return
        path = str(Path(path))
        if self.current_path == path:
            return
        self.current_path = path
        self.open_pdf(path)

    def set_annotation_tool(self, tool: str) -> None:
        self._tool = str(tool or "select_text")
        self._js_call("setTool", self._tool)

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        payload = []
        for h in (highlights or []):
            page = int(self._last_page)
            rects = []
            for rect in h.get("rects", []) or []:
                if not isinstance(rect, dict):
                    continue
                rects.append(normalize_annotation_rect(rect))
            payload.append({
                "annotation_id": str(h.get("id", "")),
                "page_index": page - 1,
                "rects": rects,
                "color": h.get("color", "#2d9cdb"),
                "opacity": float(h.get("opacity", 0.35)),
            })
        self.load_annotations(payload)

    # required bridge API
    def open_pdf(self, file_path: str, initial_page: int | None = None) -> None:
        self._js_call("openPdf", str(Path(file_path).resolve()), initial_page)

    def go_to_page(self, page_number: int) -> None:
        self._js_call("goToPage", max(1, int(page_number or 1)))

    def load_annotations(self, annotation_payload: list[dict]) -> None:
        self._pending_annotations = annotation_payload or []
        self._js_call("loadAnnotations", self._pending_annotations)

    def request_selected_text(self) -> None:
        self._js_call("requestSelectedText")

    def copy_selected_text(self) -> None:
        self._js_call("copySelectedText")

    def clear_selection(self) -> None:
        self._js_call("clearSelection")

    def _on_context_menu(self, _pos) -> None:
        if self._selection_menu_handler and self._selection_text.strip():
            self._selection_menu_handler(QCursor.pos(), self._selection_text, int(self._selection_page_info.get("page_number", self._last_page)))

    def _on_selection_changed(self, selected_text: str, page_info: dict) -> None:
        self._selection_text = str(selected_text or "")
        self._selection_page_info = page_info or {}

    def _on_annotation_created(self, payload: dict) -> None:
        if self._area_created_handler:
            page = int(payload.get("page_number", 1))
            rect = payload.get("rect") if isinstance(payload.get("rect"), dict) else None
            if rect:
                self._area_created_handler(rect, page)

    def _on_annotation_deleted(self, annotation_id: str) -> None:
        if self._highlight_hit_handler and annotation_id:
            try:
                self._highlight_hit_handler(int(annotation_id))
            except Exception:
                pass

    def _on_page_changed(self, page_number: int) -> None:
        self._last_page = max(1, int(page_number or 1))

    def _on_viewer_ready(self, _capabilities: dict) -> None:
        if self.current_path:
            self.open_pdf(self.current_path)
        if self._tool:
            self.set_annotation_tool(self._tool)
        if self._pending_annotations:
            self.load_annotations(self._pending_annotations)

    def _js_call(self, fn: str, *args) -> None:
        if not self._web:
            return
        payload = json.dumps(args)
        js = f"window.pdfHost && window.pdfHost.{fn}(...{payload});"
        self._web.page().runJavaScript(js)


PersistentPdfViewer = PdfJsViewer

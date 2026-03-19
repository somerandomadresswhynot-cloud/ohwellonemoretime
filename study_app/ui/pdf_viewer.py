from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtQml import QQmlProperty
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QShortcut,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class _ViewerFullscreenHost(QWidget):
    def __init__(self, on_esc, parent=None):
        super().__init__(parent)
        self._on_esc = on_esc

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._on_esc()
            event.accept()
            return
        super().keyPressEvent(event)


class _QuickViewerBridge(QObject):
    selectionMenuRequested = Signal(float, float, str, int)
    areaRectCreated = Signal("QVariantMap", int)
    highlightHit = Signal(int)
    pageChanged = Signal(int, float, float)

    @Slot(float, float, str, int)
    def emitSelectionMenuRequested(self, x: float, y: float, selected_text: str, page: int) -> None:
        self.selectionMenuRequested.emit(float(x), float(y), str(selected_text or ""), int(page))

    @Slot("QVariantMap", int)
    def emitAreaRectCreated(self, rect: dict, page: int) -> None:
        self.areaRectCreated.emit(rect or {}, int(page))

    @Slot(int)
    def emitHighlightHit(self, highlight_id: int) -> None:
        self.highlightHit.emit(int(highlight_id))

    @Slot(int, float, float)
    def emitPageChanged(self, page: int, x: float, y: float) -> None:
        self.pageChanged.emit(int(page), float(x), float(y))


class PersistentPdfViewer(QWidget):
    """Shared Qt Quick PDF viewer host used by Sources and Study Queue pages."""

    def __init__(self):
        super().__init__()
        self.current_path = ""
        self._last_page = 1
        self._last_location = (0.0, 0.0)
        self._selection_menu_handler = None
        self._area_created_handler = None
        self._highlight_hit_handler = None
        self._fullscreen_host = None
        self._quick_original_parent = None
        self._quick_original_layout = None
        self._annotation_tool = "select_text"
        self._interaction_mode = "text_select"
        self._fit_mode = "fit_width"
        self._cached_paths: list[str] = []

        root = QVBoxLayout(self)

        self._bridge = _QuickViewerBridge()
        self._bridge.selectionMenuRequested.connect(self._on_selection_menu_request)
        self._bridge.areaRectCreated.connect(self._on_area_rect_created)
        self._bridge.highlightHit.connect(self._on_highlight_hit)
        self._bridge.pageChanged.connect(self._on_page_changed)

        self._quick = QQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self._quick.setFocusPolicy(Qt.StrongFocus)
        self._quick.rootContext().setContextProperty("viewerBridge", self._bridge)

        qml_path = Path(__file__).with_name("qml").joinpath("PdfViewer.qml")
        if not qml_path.exists():
            self._quick = None
            label = QLabel("Qt Quick PDF view unavailable (missing QML component).", self)
            label.setAlignment(Qt.AlignCenter)
            root.addWidget(label)
            return

        self._quick.setSource(QUrl.fromLocalFile(str(qml_path)))
        status = self._quick.status()
        if status != QQuickWidget.Ready:
            self._quick = None
            label = QLabel("Qt Quick PDF view unavailable (failed to load QML).", self)
            label.setAlignment(Qt.AlignCenter)
            root.addWidget(label)
            return

        root.addWidget(self._quick)

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

        tip = QLabel("Tip: drag-select text, then right-click to highlight or Ctrl+C to copy.")
        tip.setStyleSheet("color:#8ea2da; font-size:11px;")
        root.addWidget(tip)
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self)
        self._copy_shortcut.activated.connect(self.copy_selected_text)
        self._apply_interaction_mode()

    def _root_object(self):
        return self._quick.rootObject() if self._quick is not None else None

    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler

    def _on_selection_menu_request(self, x: float, y: float, text: str, page: int) -> None:
        if not self._selection_menu_handler:
            return
        global_pos = self.mapToGlobal(QPoint(int(round(x)), int(round(y))))
        self._selection_menu_handler(global_pos, text, int(page))

    def set_area_created_handler(self, handler) -> None:
        self._area_created_handler = handler

    def _on_area_rect_created(self, norm_rect: dict, page: int) -> None:
        if self._area_created_handler:
            self._area_created_handler(norm_rect, int(page))

    def set_highlight_hit_handler(self, handler) -> None:
        self._highlight_hit_handler = handler

    def _on_highlight_hit(self, highlight_id: int) -> None:
        if self._highlight_hit_handler:
            self._highlight_hit_handler(int(highlight_id))

    def _on_page_changed(self, page: int, x: float, y: float) -> None:
        self._last_page = max(1, int(page))
        self._last_location = (float(x), float(y))

    def _set_root_prop(self, name: str, value) -> None:
        root = self._root_object()
        if root is not None:
            root.setProperty(name, value)

    def _get_root_prop(self, name: str, fallback=None):
        root = self._root_object()
        if root is None:
            return fallback
        value = QQmlProperty.read(root, name)
        return fallback if value is None else value

    def selected_text(self) -> str:
        text = self._get_root_prop("selectedText", "")
        return str(text or "").strip()

    def copy_selected_text(self) -> None:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def set_annotation_tool(self, tool: str) -> None:
        self._annotation_tool = tool
        mapped = {
            "select_text": "text_select",
            "area": "area_select",
            "pan": "pan",
            "erase": "erase",
        }
        self._interaction_mode = mapped.get(tool, "text_select")
        self._apply_interaction_mode()

    def _apply_interaction_mode(self) -> None:
        self._set_root_prop("interactionMode", self._interaction_mode)
        if self._quick is None:
            return
        if self._interaction_mode == "text_select":
            self._quick.setCursor(Qt.IBeamCursor)
        elif self._interaction_mode == "pan":
            self._quick.setCursor(Qt.OpenHandCursor)
        else:
            self._quick.setCursor(Qt.ArrowCursor)

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        self._set_root_prop("overlayHighlights", highlights or [])

    def prime_path(self, path: str) -> None:
        if not path or not Path(path).exists():
            return
        if path in self._cached_paths:
            self._cached_paths.remove(path)
        self._cached_paths.append(path)
        self._cached_paths = self._cached_paths[-12:]

    def load_if_needed(self, path: str) -> None:
        if not path or not Path(path).exists() or self._quick is None:
            return
        if path == self.current_path:
            return
        self.current_path = path
        self._last_page = 1
        self._last_location = (0.0, 0.0)
        self._set_root_prop("documentSource", QUrl.fromLocalFile(path).toString())
        self._set_root_prop("pendingPage", 1)
        self._set_root_prop("pendingLocationX", 0.0)
        self._set_root_prop("pendingLocationY", 0.0)
        self.set_multi_page_mode()
        if self._fit_mode == "fit_page":
            self.set_fit_page_mode()
        else:
            self.set_fit_mode()
        self._apply_interaction_mode()

    def set_multi_page_mode(self) -> None:
        self._set_root_prop("singlePageMode", False)

    def set_single_page_mode(self) -> None:
        self._set_root_prop("singlePageMode", True)

    def set_fit_mode(self) -> None:
        self._fit_mode = "fit_width"
        self._set_root_prop("zoomMode", "fit_width")
        self._sync_zoom_spin()

    def set_fit_page_mode(self) -> None:
        self._fit_mode = "fit_page"
        self._set_root_prop("zoomMode", "fit_page")
        self._sync_zoom_spin()

    def set_zoom(self, factor: float) -> None:
        self._fit_mode = "custom"
        self._set_root_prop("zoomMode", "custom")
        self._set_root_prop("zoomFactor", float(factor))
        self._sync_zoom_spin()

    def zoom_factor(self) -> float:
        return float(self._get_root_prop("zoomFactor", 1.0) or 1.0)

    def _sync_zoom_spin(self) -> None:
        if not hasattr(self, "zoom_pct"):
            return
        self.zoom_pct.blockSignals(True)
        self.zoom_pct.setValue(int(round(self.zoom_factor() * 100)))
        self.zoom_pct.blockSignals(False)

    def set_page(self, page: int, location: tuple[float, float] | None = None) -> None:
        x, y = location if location else (0.0, 0.0)
        self._last_page = max(1, int(page))
        self._last_location = (float(x), float(y))
        self._set_root_prop("pendingPage", self._last_page)
        self._set_root_prop("pendingLocationX", float(x))
        self._set_root_prop("pendingLocationY", float(y))

    def view_state(self) -> dict:
        page = int(self._get_root_prop("currentPage", self._last_page) or self._last_page)
        x = float(self._get_root_prop("locationX", self._last_location[0]) or self._last_location[0])
        y = float(self._get_root_prop("locationY", self._last_location[1]) or self._last_location[1])
        return {"page": max(1, page), "location": (x, y)}

    def toggle_fullscreen(self) -> None:
        if self._quick is None:
            return
        if self._fullscreen_host is None:
            self._quick_original_parent = self._quick.parentWidget()
            self._quick_original_layout = self.layout()
            if self._quick_original_layout:
                self._quick_original_layout.removeWidget(self._quick)
            self._fullscreen_host = _ViewerFullscreenHost(self.toggle_fullscreen)
            self._fullscreen_host.setWindowTitle("PDF Viewer")
            self._fullscreen_host.setWindowFlag(Qt.Window)
            lay = QVBoxLayout(self._fullscreen_host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self._quick)
            self._fullscreen_host.showFullScreen()
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Exit Viewer Full Screen")
        else:
            host = self._fullscreen_host
            host.layout().removeWidget(self._quick)
            if self._quick_original_layout:
                self._quick_original_layout.insertWidget(0, self._quick)
            self._quick.setParent(self._quick_original_parent)
            host.close()
            host.deleteLater()
            self._fullscreen_host = None
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Viewer Full Screen")

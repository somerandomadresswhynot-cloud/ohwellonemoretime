from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path
import warnings

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
except Exception:  # runtime guard if QtPdf missing
    QPdfDocument = None
    QPdfView = None


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


class _AnnotationOverlay(QWidget):
    def __init__(self, owner: "PersistentPdfViewer", parent: QWidget):
        super().__init__(parent)
        self._owner = owner
        self._drag_start = None
        self._drag_end = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

    def _draft_rect(self) -> QRectF | None:
        if self._drag_start is None or self._drag_end is None:
            return None
        return QRectF(self._drag_start, self._drag_end).normalized()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        for entry in self._owner._overlay_highlights:
            color = QColor(entry.get("color", "#2d9cdb"))
            color.setAlpha(max(25, min(220, int(255 * float(entry.get("opacity", 0.35))))))
            p.setBrush(color)
            pen = QPen(QColor(entry.get("color", "#2d9cdb")))
            pen.setWidth(2)
            p.setPen(pen)
            for norm_rect in entry.get("rects", []):
                p.drawRoundedRect(self._owner.overlay_rect_to_viewport(norm_rect), 3, 3)

        draft = self._draft_rect()
        if draft and self._owner._interaction_mode == "area_select":
            p.setBrush(QColor(45, 156, 219, 65))
            p.setPen(QPen(QColor("#7ecbff"), 2, Qt.DashLine))
            p.drawRoundedRect(draft, 2, 2)

    def mousePressEvent(self, event):
        mode = self._owner._interaction_mode
        if event.button() == Qt.LeftButton and mode == "area_select":
            self._drag_start = event.position()
            self._drag_end = event.position()
            self.update()
            return
        if event.button() == Qt.LeftButton and mode == "erase":
            clicked = event.position()
            for entry in reversed(self._owner._overlay_highlights):
                for norm_rect in entry.get("rects", []):
                    if self._owner.overlay_rect_to_viewport(norm_rect).contains(clicked):
                        if self._owner._highlight_hit_handler:
                            self._owner._highlight_hit_handler(int(entry.get("id", 0)))
                        self._owner._debug(
                            "overlay.erase.hit",
                            highlight_id=int(entry.get("id", 0)),
                            x=round(float(clicked.x()), 2),
                            y=round(float(clicked.y()), 2),
                        )
                        event.accept()
                        return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._owner._interaction_mode == "area_select" and self._drag_start is not None:
            self._drag_end = event.position()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._owner._interaction_mode == "area_select" and event.button() == Qt.LeftButton and self._drag_start is not None:
            self._drag_end = event.position()
            rect = self._draft_rect()
            self._drag_start = None
            self._drag_end = None
            self.update()
            if rect is None or rect.width() < 6 or rect.height() < 6:
                return
            if self._owner._area_created_handler:
                page = int(self._owner.view_state().get("page", 1))
                stored_rect = self._owner.viewport_rect_to_stored_rect(rect, page)
                self._owner._debug(
                    "overlay.area.created",
                    page=page,
                    viewport_rect={
                        "x": round(float(rect.x()), 2),
                        "y": round(float(rect.y()), 2),
                        "w": round(float(rect.width()), 2),
                        "h": round(float(rect.height()), 2),
                    },
                    stored_rect=stored_rect,
                )
                self._owner._area_created_handler(stored_rect, page)
            return
        super().mouseReleaseEvent(event)


class PersistentPdfViewer(QWidget):
    def __init__(self):
        super().__init__()
        self._debug_enabled = os.environ.get("STUDY_APP_PDF_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self.current_path = ""
        self._view = None
        self._label = QLabel("PDF view unavailable (QtPdf missing).")
        self._label.setAlignment(Qt.AlignCenter)
        self._last_page = 1
        self._last_location = (0.0, 0.0)
        self._selection_menu_handler = None
        self._context_menu_connected = False
        self._viewport_context_menu_connected = False
        self._page_nav = None
        self._page_changed_connected = False

        self._doc_cache: OrderedDict[str, QPdfDocument] = OrderedDict()
        self._cache_limit = 8
        self._fullscreen_host = None
        self._view_original_parent = None
        self._view_original_layout = None
        self._annotation_tool = "select_text"
        self._interaction_mode = "text_select"
        self._area_created_handler = None
        self._highlight_hit_handler = None
        self._overlay = None
        self._overlay_highlights: list[dict] = []
        self._viewport_host = None

        root = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            viewport_host = QWidget(self)
            self._viewport_host = viewport_host
            viewport_layout = QVBoxLayout(viewport_host)
            viewport_layout.setContentsMargins(0, 0, 0, 0)

            self._view = QPdfView(viewport_host)
            self._enable_text_selection_mode()
            self._apply_default_view_mode()
            viewport_layout.addWidget(self._view)
            self._overlay = _AnnotationOverlay(self, self._view.viewport())
            self._overlay.raise_()
            self._overlay.resize(self._view.viewport().size())
            self._view.viewport().installEventFilter(self)
            self._view.installEventFilter(self)
            root.addWidget(viewport_host)

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
            tip = QLabel("Tip: select text, then right-click for highlight menu (or Ctrl+C + Add Highlight).")
            tip.setStyleSheet("color:#8ea2da; font-size:11px;")
            root.addWidget(tip)
        else:
            root.addWidget(self._label)

    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler
        if not self._view:
            return
        viewport = self._view.viewport()
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        viewport.setContextMenuPolicy(Qt.CustomContextMenu)
        if self._context_menu_connected:
            self._safe_disconnect(self._view.customContextMenuRequested, self._on_context_menu)
            self._context_menu_connected = False
        if self._viewport_context_menu_connected:
            self._safe_disconnect(viewport.customContextMenuRequested, self._on_viewport_context_menu)
            self._viewport_context_menu_connected = False
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        viewport.customContextMenuRequested.connect(self._on_viewport_context_menu)
        self._context_menu_connected = True
        self._viewport_context_menu_connected = True

    def _on_context_menu(self, _pos) -> None:
        self._debug("context_menu.view")
        if self._selection_menu_handler:
            self._selection_menu_handler(QCursor.pos(), self.selected_text(), int(self.view_state().get("page", 1)))

    def _on_viewport_context_menu(self, pos) -> None:
        self._debug("context_menu.viewport", x=int(pos.x()), y=int(pos.y()))
        if self._selection_menu_handler:
            global_pos = self._view.viewport().mapToGlobal(pos)
            self._selection_menu_handler(global_pos, self.selected_text(), int(self.view_state().get("page", 1)))

    def _debug(self, event: str, **payload) -> None:
        if not self._debug_enabled:
            return
        details = " ".join(f"{k}={payload[k]!r}" for k in sorted(payload))
        print(f"[pdf_viewer] {event} {details}".rstrip())

    def eventFilter(self, watched, event):
        if self._view and watched is self._view:
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseMove, QEvent.ContextMenu):
                self._debug(
                    "event.view",
                    type=int(event.type()),
                    mode=self._interaction_mode,
                )
        if self._overlay and self._view and watched is self._view.viewport():
            if event.type() in (QEvent.Resize, QEvent.Show):
                self._overlay.resize(self._view.viewport().size())
                self._overlay.raise_()
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseMove, QEvent.ContextMenu):
                pos = getattr(event, "position", lambda: QPointF(-1, -1))()
                self._debug(
                    "event.viewport",
                    type=int(event.type()),
                    mode=self._interaction_mode,
                    x=round(float(pos.x()), 2),
                    y=round(float(pos.y()), 2),
                    page=int(self.view_state().get("page", 1)),
                )
                if event.type() == QEvent.MouseButtonRelease and self._interaction_mode == "text_select":
                    selected = self.selected_text()
                    self._debug("selection.after_mouse_release", chars=len(selected))
            if event.type() == QEvent.KeyPress and self._interaction_mode == "text_select":
                key = getattr(event, "key", lambda: -1)()
                mods = int(getattr(event, "modifiers", lambda: Qt.NoModifier)())
                if key == Qt.Key_C and mods & Qt.ControlModifier:
                    if self.copy_selected_text():
                        event.accept()
                        return True
        return super().eventFilter(watched, event)

    def set_area_created_handler(self, handler) -> None:
        self._area_created_handler = handler

    def set_highlight_hit_handler(self, handler) -> None:
        self._highlight_hit_handler = handler

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        self._overlay_highlights = highlights or []
        if self._overlay:
            self._overlay.update()

    def _page_size_points(self, page: int) -> tuple[float, float]:
        if not self._view or page < 1:
            return (0.0, 0.0)
        doc = self._view.document()
        if doc is None:
            return (0.0, 0.0)
        try:
            size = doc.pagePointSize(int(page) - 1)
            return (max(0.0, float(size.width())), max(0.0, float(size.height())))
        except Exception:
            return (0.0, 0.0)

    def viewport_rect_to_stored_rect(self, rect: QRectF, page: int) -> dict:
        if not self._view or not self._overlay:
            return {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0, "coord": "viewport_norm_v1"}
        zoom = max(0.0001, float(self.zoom_factor()))
        nav = self._view.pageNavigator()
        try:
            loc = nav.currentLocation()
            loc_x = float(loc.x())
            loc_y = float(loc.y())
        except Exception:
            loc_x = 0.0
            loc_y = 0.0
        page_w, page_h = self._page_size_points(page)
        if page_w <= 0.0 or page_h <= 0.0:
            return self._viewport_norm_rect(rect)
        doc_left = loc_x + (float(rect.left()) / zoom)
        doc_top = loc_y + (float(rect.top()) / zoom)
        doc_w = float(rect.width()) / zoom
        doc_h = float(rect.height()) / zoom
        x = max(0.0, min(1.0, doc_left / page_w))
        y = max(0.0, min(1.0, doc_top / page_h))
        w = max(0.0, min(1.0 - x, doc_w / page_w))
        h = max(0.0, min(1.0 - y, doc_h / page_h))
        return {
            "coord": "page_norm_v1",
            "x": round(x, 6),
            "y": round(y, 6),
            "w": round(w, 6),
            "h": round(h, 6),
        }

    def _viewport_norm_rect(self, rect: QRectF) -> dict:
        overlay_w = max(1.0, float(self._overlay.width() if self._overlay else 1.0))
        overlay_h = max(1.0, float(self._overlay.height() if self._overlay else 1.0))
        x = max(0.0, min(1.0, float(rect.left()) / overlay_w))
        y = max(0.0, min(1.0, float(rect.top()) / overlay_h))
        w = max(0.0, min(1.0 - x, float(rect.width()) / overlay_w))
        h = max(0.0, min(1.0 - y, float(rect.height()) / overlay_h))
        return {
            "coord": "viewport_norm_v1",
            "x": round(x, 6),
            "y": round(y, 6),
            "w": round(w, 6),
            "h": round(h, 6),
        }

    def overlay_rect_to_viewport(self, stored_rect: dict) -> QRectF:
        x = float(stored_rect.get("x", 0.0))
        y = float(stored_rect.get("y", 0.0))
        w = float(stored_rect.get("w", 0.0))
        h = float(stored_rect.get("h", 0.0))
        coord = str(stored_rect.get("coord") or "")
        if not self._overlay or not self._view:
            return QRectF()
        if coord != "page_norm_v1":
            return QRectF(x * self._overlay.width(), y * self._overlay.height(), w * self._overlay.width(), h * self._overlay.height())
        page = int(self.view_state().get("page", 1))
        page_w, page_h = self._page_size_points(page)
        if page_w <= 0.0 or page_h <= 0.0:
            return QRectF(x * self._overlay.width(), y * self._overlay.height(), w * self._overlay.width(), h * self._overlay.height())
        nav = self._view.pageNavigator()
        try:
            loc = nav.currentLocation()
            loc_x = float(loc.x())
            loc_y = float(loc.y())
        except Exception:
            loc_x = 0.0
            loc_y = 0.0
        zoom = max(0.0001, float(self.zoom_factor()))
        doc_left = x * page_w
        doc_top = y * page_h
        doc_w = w * page_w
        doc_h = h * page_h
        px_left = (doc_left - loc_x) * zoom
        px_top = (doc_top - loc_y) * zoom
        px_w = doc_w * zoom
        px_h = doc_h * zoom
        self._debug(
            "overlay.reproject",
            page=page,
            coord=coord,
            doc_left=round(doc_left, 2),
            doc_top=round(doc_top, 2),
            zoom=round(zoom, 3),
            viewport_left=round(px_left, 2),
            viewport_top=round(px_top, 2),
            viewport_w=round(px_w, 2),
            viewport_h=round(px_h, 2),
        )
        return QRectF(px_left, px_top, px_w, px_h)

    def selected_text(self) -> str:
        if not self._view:
            return ""
        # Try direct selection APIs first.
        try:
            if hasattr(self._view, "selectedText"):
                txt = self._view.selectedText()
                if txt:
                    return str(txt).strip()
        except Exception:
            pass
        try:
            if hasattr(self._view, "selection"):
                sel = self._view.selection()
                txt = getattr(sel, "text", lambda: "")()
                if txt:
                    return str(txt).strip()
        except Exception:
            pass
        return ""

    def copy_selected_text(self) -> bool:
        text = self.selected_text()
        if not text:
            self._debug("copy_selected_text.empty_selection")
            return False
        try:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
            self._debug("copy_selected_text.ok", chars=len(text))
            return True
        except Exception as exc:
            self._debug("copy_selected_text.failed", error=str(exc))
            return False

    def _enable_text_selection_mode(self) -> None:
        if not self._view:
            return
        try:
            enum_cls = getattr(QPdfView, "SelectionMode", None)
            if enum_cls is not None:
                for member in ("TextSelection", "TextSelector", "SelectText"):
                    if hasattr(enum_cls, member):
                        self._view.setSelectionMode(getattr(enum_cls, member))
                        return
        except Exception:
            pass
        try:
            if hasattr(self._view, "setSelectionEnabled"):
                self._view.setSelectionEnabled(True)
        except Exception:
            pass

    def _disable_text_selection_mode(self) -> None:
        if not self._view:
            return
        try:
            enum_cls = getattr(QPdfView, "SelectionMode", None)
            if enum_cls is not None:
                for member in ("NoSelection", "None_"):
                    if hasattr(enum_cls, member):
                        self._view.setSelectionMode(getattr(enum_cls, member))
                        return
        except Exception:
            pass
        try:
            if hasattr(self._view, "setSelectionEnabled"):
                self._view.setSelectionEnabled(False)
        except Exception:
            pass

    def set_annotation_tool(self, tool: str) -> None:
        self._annotation_tool = tool
        mapped = {
            "select_text": "text_select",
            "area": "area_select",
            "pan": "pan",
            "erase": "erase",
        }
        self._interaction_mode = mapped.get(tool, "text_select")
        if not self._view:
            return
        if self._interaction_mode == "text_select":
            self._enable_text_selection_mode()
            self._view.setCursor(Qt.IBeamCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._debug("tool.changed", tool=tool, mode=self._interaction_mode, overlay_transparent=True)
            return
        if self._interaction_mode == "pan":
            self._disable_text_selection_mode()
            self._view.setCursor(Qt.OpenHandCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._debug("tool.changed", tool=tool, mode=self._interaction_mode, overlay_transparent=True)
            return
        # Area + erase modes are handled by workspace overlays; keep pointer neutral here.
        self._disable_text_selection_mode()
        self._view.setCursor(Qt.ArrowCursor)
        if self._overlay:
            self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._overlay.raise_()
            self._overlay.update()
        self._debug("tool.changed", tool=tool, mode=self._interaction_mode, overlay_transparent=False)

    def _attach_page_changed(self, doc: QPdfDocument) -> None:
        nav = self._view.pageNavigator()
        if self._page_changed_connected and self._page_nav is not None:
            self._safe_disconnect(self._page_nav.currentPageChanged, self._on_page_changed)
            self._page_changed_connected = False
            self._page_nav = None
        try:
            nav.currentPageChanged.connect(self._on_page_changed)
            self._page_nav = nav
            self._page_changed_connected = True
        except Exception:
            pass

    def _safe_disconnect(self, signal, slot) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                signal.disconnect(slot)
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
        self.set_single_page_mode()
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
            host_widget = self._viewport_host or self._view
            self._view_original_parent = host_widget.parentWidget()
            self._view_original_layout = self.layout()
            if self._view_original_layout:
                self._view_original_layout.removeWidget(host_widget)
            self._fullscreen_host = _ViewerFullscreenHost(self.toggle_fullscreen)
            self._fullscreen_host.setWindowTitle("PDF Viewer")
            self._fullscreen_host.setWindowFlag(Qt.Window)
            lay = QVBoxLayout(self._fullscreen_host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(host_widget)
            self._fullscreen_host.showFullScreen()
            if hasattr(self, "fullscreen_btn"):
                self.fullscreen_btn.setText("Exit Viewer Full Screen")
        else:
            host = self._fullscreen_host
            host_widget = self._viewport_host or self._view
            host.layout().removeWidget(host_widget)
            if self._view_original_layout:
                self._view_original_layout.insertWidget(0, host_widget)
            host_widget.setParent(self._view_original_parent)
            host.close()
            host.deleteLater()
            self._fullscreen_host = None
            if self._overlay:
                self._overlay.resize(self._view.viewport().size())
                self._overlay.raise_()
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

    def closeEvent(self, event):
        if self._page_changed_connected and self._page_nav is not None:
            self._safe_disconnect(self._page_nav.currentPageChanged, self._on_page_changed)
            self._page_changed_connected = False
            self._page_nav = None
        if self._context_menu_connected and self._view is not None:
            self._safe_disconnect(self._view.customContextMenuRequested, self._on_context_menu)
            self._context_menu_connected = False
        if self._viewport_context_menu_connected and self._view is not None:
            self._safe_disconnect(self._view.viewport().customContextMenuRequested, self._on_viewport_context_menu)
            self._viewport_context_menu_connected = False
        super().closeEvent(event)

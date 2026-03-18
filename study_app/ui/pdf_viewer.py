from __future__ import annotations

from collections import OrderedDict
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

    def _norm_to_px_rect(self, norm_rect: dict) -> QRectF:
        x = float(norm_rect.get("x", 0.0))
        y = float(norm_rect.get("y", 0.0))
        w = float(norm_rect.get("w", 0.0))
        h = float(norm_rect.get("h", 0.0))
        return QRectF(x * self.width(), y * self.height(), w * self.width(), h * self.height())

    def _px_to_norm_rect(self, rect: QRectF) -> dict:
        if self.width() <= 0 or self.height() <= 0:
            return {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
        x = max(0.0, min(1.0, rect.left() / self.width()))
        y = max(0.0, min(1.0, rect.top() / self.height()))
        w = max(0.0, min(1.0 - x, rect.width() / self.width()))
        h = max(0.0, min(1.0 - y, rect.height() / self.height()))
        return {"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)}

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
                p.drawRoundedRect(self._norm_to_px_rect(norm_rect), 3, 3)

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
                    if self._norm_to_px_rect(norm_rect).contains(clicked):
                        if self._owner._highlight_hit_handler:
                            self._owner._highlight_hit_handler(int(entry.get("id", 0)))
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
                self._owner._area_created_handler(self._px_to_norm_rect(rect), int(self._owner.view_state().get("page", 1)))
            return
        super().mouseReleaseEvent(event)


class PersistentPdfViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.current_path = ""
        self._view = None
        self._label = QLabel("PDF view unavailable (QtPdf missing).")
        self._label.setAlignment(Qt.AlignCenter)
        self._last_page = 1
        self._last_location = (0.0, 0.0)
        self._selection_menu_handler = None
        self._context_menu_connected = False
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
        self._selection_enabled = False

        root = QVBoxLayout(self)
        if QPdfDocument and QPdfView:
            viewport_host = QWidget(self)
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
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        if self._context_menu_connected:
            self._safe_disconnect(self._view.customContextMenuRequested, self._on_context_menu)
            self._context_menu_connected = False
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._context_menu_connected = True

    def _on_context_menu(self, _pos) -> None:
        if self._selection_menu_handler:
            self._selection_menu_handler(QCursor.pos(), self.selected_text(), int(self.view_state().get("page", 1)))

    def eventFilter(self, watched, event):
        if self._overlay and self._view and watched is self._view.viewport():
            if event.type() in (QEvent.Resize, QEvent.Show):
                self._overlay.resize(self._view.viewport().size())
                self._overlay.raise_()
        return super().eventFilter(watched, event)

    def set_area_created_handler(self, handler) -> None:
        self._area_created_handler = handler

    def set_highlight_hit_handler(self, handler) -> None:
        self._highlight_hit_handler = handler

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        self._overlay_highlights = highlights or []
        if self._overlay:
            self._overlay.update()

    def selection_diagnostics(self) -> dict:
        return {
            "enabled": bool(self._selection_enabled),
            "interaction_mode": self._interaction_mode,
            "tool": self._annotation_tool,
        }

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
        # Fallback: clipboard text (user can Ctrl+C selected fragment).
        try:
            from PySide6.QtWidgets import QApplication

            return (QApplication.clipboard().text() or "").strip()
        except Exception:
            return ""

    def _enable_text_selection_mode(self) -> None:
        if not self._view:
            return
        enabled = False
        try:
            enum_cls = getattr(QPdfView, "SelectionMode", None)
            if enum_cls is not None:
                for member in ("TextSelection", "TextSelector", "SelectText", "TextSelect"):
                    if hasattr(enum_cls, member):
                        self._view.setSelectionMode(getattr(enum_cls, member))
                        enabled = True
                        break
                if not enabled:
                    try:
                        self._view.setSelectionMode(enum_cls(1))
                        enabled = True
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            if hasattr(self._view, "setSelectionEnabled"):
                self._view.setSelectionEnabled(True)
                enabled = True
        except Exception:
            pass
        try:
            self._view.setFocusPolicy(Qt.StrongFocus)
            self._view.setFocus()
        except Exception:
            pass
        self._selection_enabled = enabled

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
            self.set_single_page_mode()
            self._view.setCursor(Qt.IBeamCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            return
        if self._interaction_mode == "pan":
            self._disable_text_selection_mode()
            self.set_multi_page_mode()
            self._view.setCursor(Qt.OpenHandCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            return
        # Area + erase modes are handled by workspace overlays; keep pointer neutral here.
        self._disable_text_selection_mode()
        self.set_multi_page_mode()
        self._view.setCursor(Qt.ArrowCursor)
        if self._overlay:
            self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._overlay.raise_()
            self._overlay.update()

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
            self._fullscreen_host = _ViewerFullscreenHost(self.toggle_fullscreen)
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

    def closeEvent(self, event):
        if self._page_changed_connected and self._page_nav is not None:
            self._safe_disconnect(self._page_nav.currentPageChanged, self._on_page_changed)
            self._page_changed_connected = False
            self._page_nav = None
        if self._context_menu_connected and self._view is not None:
            self._safe_disconnect(self._view.customContextMenuRequested, self._on_context_menu)
            self._context_menu_connected = False
        super().closeEvent(event)

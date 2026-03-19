from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QPen, QShortcut, QWheelEvent
from PySide6.QtQml import QQmlProperty
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
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


class _PdfQuickWidget(QQuickWidget):
    """Quick widget that keeps wheel scrolling local to the PDF surface."""

    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class _AnnotationOverlay(QWidget):
    def __init__(self, owner: "PersistentPdfViewer", parent: QWidget):
        super().__init__(parent)
        self._owner = owner
        self._drag_start = None
        self._drag_end = None
        self._active_highlight_id = 0
        self._edit_mode = ""
        self._last_pos = None
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
        self._paint_active_handles(p)

    def _active_rect(self) -> tuple[dict, QRectF] | tuple[None, None]:
        if not self._active_highlight_id:
            return None, None
        for entry in self._owner._overlay_highlights:
            if int(entry.get("id", 0)) != int(self._active_highlight_id):
                continue
            rects = entry.get("rects", [])
            if not rects:
                return entry, None
            return entry, self._norm_to_px_rect(rects[0])
        return None, None

    def _paint_active_handles(self, p: QPainter) -> None:
        _entry, rect = self._active_rect()
        if rect is None:
            return
        p.setPen(QPen(QColor("#f8fdff"), 1))
        p.setBrush(QColor("#4ec0ff"))
        for handle in self._handle_rects(rect).values():
            p.drawRect(handle)

    def _handle_rects(self, rect: QRectF) -> dict[str, QRectF]:
        hs = 8.0
        return {
            "nw": QRectF(rect.left() - hs, rect.top() - hs, hs * 2, hs * 2),
            "ne": QRectF(rect.right() - hs, rect.top() - hs, hs * 2, hs * 2),
            "sw": QRectF(rect.left() - hs, rect.bottom() - hs, hs * 2, hs * 2),
            "se": QRectF(rect.right() - hs, rect.bottom() - hs, hs * 2, hs * 2),
        }

    def _hit_highlight(self, pos) -> tuple[dict | None, QRectF | None]:
        for entry in reversed(self._owner._overlay_highlights):
            for norm_rect in entry.get("rects", []):
                px_rect = self._norm_to_px_rect(norm_rect)
                if px_rect.contains(pos):
                    return entry, px_rect
        return None, None

    def _persist_active_rect(self, rect: QRectF) -> None:
        entry, _ = self._active_rect()
        if entry is None or self._owner._highlight_rect_changed_handler is None:
            return
        hid = int(entry.get("id", 0))
        self._owner._highlight_rect_changed_handler(hid, self._px_to_norm_rect(rect), int(self._owner.view_state().get("page", 1)))

    def mousePressEvent(self, event):
        mode = self._owner._interaction_mode
        if event.button() == Qt.RightButton:
            entry, _ = self._hit_highlight(event.position())
            if entry and self._owner._highlight_context_menu_handler:
                self._active_highlight_id = int(entry.get("id", 0))
                self.update()
                self._owner._highlight_context_menu_handler(
                    self.mapToGlobal(event.position().toPoint()),
                    int(entry.get("id", 0)),
                    int(self._owner.view_state().get("page", 1)),
                )
                event.accept()
                return
        if event.button() == Qt.LeftButton and mode == "pan":
            self._edit_mode = "pan_drag"
            self._last_pos = event.position()
            event.accept()
            return
        if event.button() == Qt.LeftButton and mode == "area_select":
            entry, hit_rect = self._hit_highlight(event.position())
            if entry is not None and hit_rect is not None:
                self._active_highlight_id = int(entry.get("id", 0))
                for name, hrect in self._handle_rects(hit_rect).items():
                    if hrect.contains(event.position()):
                        self._edit_mode = f"resize_{name}"
                        self._last_pos = event.position()
                        event.accept()
                        self.update()
                        return
                if hit_rect.contains(event.position()):
                    self._edit_mode = "move"
                    self._last_pos = event.position()
                    event.accept()
                    self.update()
                    return
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
        if self._edit_mode == "pan_drag" and self._last_pos is not None:
            delta = event.position() - self._last_pos
            self._last_pos = event.position()
            if self._owner._pan_delta_handler:
                self._owner._pan_delta_handler(float(delta.x()), float(delta.y()), self.mapToGlobal(event.position().toPoint()))
            event.accept()
            return
        if self._edit_mode and self._active_highlight_id:
            _entry, rect = self._active_rect()
            if rect is None or self._last_pos is None:
                return
            delta = event.position() - self._last_pos
            self._last_pos = event.position()
            if self._edit_mode == "move":
                rect.translate(delta.x(), delta.y())
            elif self._edit_mode.startswith("resize_"):
                corner = self._edit_mode.split("_", 1)[1]
                if "n" in corner:
                    rect.setTop(rect.top() + delta.y())
                if "s" in corner:
                    rect.setBottom(rect.bottom() + delta.y())
                if "w" in corner:
                    rect.setLeft(rect.left() + delta.x())
                if "e" in corner:
                    rect.setRight(rect.right() + delta.x())
            rect = rect.normalized()
            rect.setLeft(max(0.0, rect.left()))
            rect.setTop(max(0.0, rect.top()))
            rect.setRight(min(float(self.width()), rect.right()))
            rect.setBottom(min(float(self.height()), rect.bottom()))
            self._persist_active_rect(rect)
            self.update()
            return
        if self._owner._interaction_mode == "area_select" and self._drag_start is not None:
            self._drag_end = event.position()
            self.update()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._edit_mode == "pan_drag":
            self._edit_mode = ""
            self._last_pos = None
            event.accept()
            return
        if self._edit_mode:
            self._edit_mode = ""
            self._last_pos = None
            event.accept()
            return
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
        self._overlay_highlights: list[dict] = []
        self._context_menu_connected = False
        self._overlay = None
        self._highlight_context_menu_handler = None
        self._highlight_rect_changed_handler = None
        self._pan_delta_handler = self._pan_by_delta

        root = QVBoxLayout(self)

        self._quick = _PdfQuickWidget(self)
        self._quick.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self._quick.setFocusPolicy(Qt.StrongFocus)
        self._quick.installEventFilter(self)

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
        self._overlay = _AnnotationOverlay(self, self)
        self._sync_overlay_geometry()
        self._overlay.raise_()

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
        QTimer.singleShot(0, self._deferred_initial_fit)
        QTimer.singleShot(75, self._deferred_initial_fit)

    def _root_object(self):
        return self._quick.rootObject() if self._quick is not None else None

    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler
        if self._quick is None:
            return
        self._quick.setContextMenuPolicy(Qt.CustomContextMenu)
        if self._context_menu_connected:
            try:
                self._quick.customContextMenuRequested.disconnect(self._on_context_menu)
            except Exception:
                pass
        self._quick.customContextMenuRequested.connect(self._on_context_menu)
        self._context_menu_connected = True

    def _on_context_menu(self, pos: QPoint) -> None:
        hit_id = self._overlay_hit_id_at_global(self._quick.mapToGlobal(pos))
        if hit_id and self._highlight_context_menu_handler:
            self._highlight_context_menu_handler(self._quick.mapToGlobal(pos), int(hit_id), int(self.view_state().get("page", 1)))
            return
        if not self._selection_menu_handler:
            return
        global_pos = self._quick.mapToGlobal(pos)
        self._selection_menu_handler(global_pos, self.selected_text(), int(self.view_state().get("page", 1)))

    def set_area_created_handler(self, handler) -> None:
        self._area_created_handler = handler

    def set_highlight_hit_handler(self, handler) -> None:
        self._highlight_hit_handler = handler

    def set_highlight_context_menu_handler(self, handler) -> None:
        self._highlight_context_menu_handler = handler

    def set_highlight_rect_changed_handler(self, handler) -> None:
        self._highlight_rect_changed_handler = handler

    def _set_root_prop(self, name: str, value) -> None:
        root = self._root_object()
        if root is not None:
            root.setProperty(name, value)

    def _call_root(self, name: str, *args) -> bool:
        root = self._root_object()
        if root is None:
            return False
        fn = getattr(root, name, None)
        if not callable(fn):
            return False
        try:
            fn(*args)
            return True
        except Exception:
            return False

    def eventFilter(self, watched, event):
        if watched is self._quick and self._overlay is not None and event.type() in (QEvent.Resize, QEvent.Show):
            self._sync_overlay_geometry()
            self._overlay.raise_()
        return super().eventFilter(watched, event)

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
        if not self._call_root("copySelection"):
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
        if self._quick is None:
            return
        if self._interaction_mode == "text_select":
            self._quick.setCursor(Qt.IBeamCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        elif self._interaction_mode == "pan":
            self._quick.setCursor(Qt.OpenHandCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                self._overlay.raise_()
                self._overlay.update()
        else:
            self._quick.setCursor(Qt.ArrowCursor)
            if self._overlay:
                self._overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                self._overlay.raise_()
                self._overlay.update()

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        self._overlay_highlights = highlights or []
        if self._overlay:
            self._overlay.update()

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
        self._call_root("jumpToPage", 1)
        self.set_multi_page_mode()
        self.set_fit_mode() if self._fit_mode != "fit_page" else self.set_fit_page_mode()
        self._apply_interaction_mode()

    def set_multi_page_mode(self) -> None:
        return

    def set_single_page_mode(self) -> None:
        return

    def set_fit_mode(self) -> None:
        self._fit_mode = "fit_width"
        if self.width() > 40 and self.height() > 40:
            self._call_root("fitToWidth")
        self._sync_zoom_spin()

    def set_fit_page_mode(self) -> None:
        self._fit_mode = "fit_page"
        if self.width() > 40 and self.height() > 40:
            self._call_root("fitToPage")
        self._sync_zoom_spin()

    def set_zoom(self, factor: float) -> None:
        self._fit_mode = "custom"
        self._call_root("setRenderScale", float(factor))
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
        if location:
            self._call_root("jumpToLocation", self._last_page, float(x), float(y), self.zoom_factor())
        else:
            self._call_root("jumpToPage", self._last_page)

    def view_state(self) -> dict:
        page = int(self._get_root_prop("currentPage", self._last_page) or self._last_page)
        x = float(self._last_location[0])
        y = float(self._last_location[1])
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
            QTimer.singleShot(0, self._deferred_initial_fit)
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
            QTimer.singleShot(0, self._deferred_initial_fit)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._deferred_initial_fit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay_geometry()
        QTimer.singleShot(0, self._deferred_initial_fit)

    def _deferred_initial_fit(self) -> None:
        if self._quick is None or not self.isVisible():
            return
        if self._quick.width() < 40 or self._quick.height() < 40:
            return
        if self._fit_mode == "fit_page":
            self._call_root("fitToPage")
        elif self._fit_mode == "custom":
            self._call_root("setRenderScale", self.zoom_factor())
        else:
            self._call_root("fitToWidth")
        self._sync_overlay_geometry()

    def _sync_overlay_geometry(self) -> None:
        if self._overlay is None or self._quick is None:
            return
        self._overlay.setGeometry(self._quick.geometry())

    def _overlay_hit_id_at_global(self, global_pos: QPoint) -> int:
        if self._overlay is None or not self._overlay.isVisible():
            return 0
        local = self._overlay.mapFromGlobal(global_pos)
        for entry in reversed(self._overlay_highlights):
            for norm_rect in entry.get("rects", []):
                x = float(norm_rect.get("x", 0.0)) * self._overlay.width()
                y = float(norm_rect.get("y", 0.0)) * self._overlay.height()
                w = float(norm_rect.get("w", 0.0)) * self._overlay.width()
                h = float(norm_rect.get("h", 0.0)) * self._overlay.height()
                if QRectF(x, y, w, h).contains(local):
                    return int(entry.get("id", 0))
        return 0

    def _pan_by_delta(self, _dx: float, dy: float, global_pos: QPoint) -> None:
        if self._quick is None:
            return
        local = self._quick.mapFromGlobal(global_pos)
        pixel_delta = QPoint(0, int(-dy))
        angle_delta = QPoint(0, int(-dy * 8.0))
        ev = QWheelEvent(
            local,
            global_pos,
            pixel_delta,
            angle_delta,
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QCoreApplication.sendEvent(self._quick, ev)

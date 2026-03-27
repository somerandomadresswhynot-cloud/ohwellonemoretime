from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QCursor, QPainter, QPen, QLinearGradient
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from study_app.persistence.repositories import HighlightRepo, OutlineRepo, ReviewRepo, SettingsRepo, SourceRepo, _derive_due_at_from_events
from study_app.pdf.pdf_service import PdfService
from study_app.services.outline_service import entries_to_text
from study_app.services.queue_planner import plan_session_queue
from study_app.services.scheduler import allocate_new_units, recommend_new_units_with_guardrail, retention_estimate
from study_app.services.fsrs_scheduler import DEFAULT_FSRS_PARAMETERS, schedule_next_review
from study_app.services.queue_drift import should_rebuild_for_estimate_drift
from study_app.services.runtime_estimator import RuntimeEstimationModel, build_runtime_estimation_model
from study_app.domain.models import iso_utc, now_utc, parse_iso_to_utc
from study_app.ui.dialogs import HintMarkdownDialog, OutlineEditorDialog, RecallNoteDialog, ReviewHistoryDialog, SourceMetadataDialog
from study_app.ui.pdf_viewer import PersistentPdfViewer
from study_app.services.day_window import day_window_for_offset, is_valid_gmt_offset, normalized_gmt_offset, parse_gmt_offset
from study_app.services.time_format import format_minutes_whole


_SESSION_QUEUE_SNAPSHOT = {
    "date": "",
    "daily_minutes": None,
    "unit_ids": [],
    "manual_unit_ids": [],
    "projected_seconds": None,
    "estimator_signature": "",
    "last_rebuild_at": "",
    "last_rebuild_reason": "",
}


def _load_session_queue_snapshot(today: str, daily_minutes: int) -> dict:
    data = _SESSION_QUEUE_SNAPSHOT
    if str(data.get("date") or "") != today:
        return {
            "date": today,
            "daily_minutes": int(daily_minutes),
            "unit_ids": [],
            "manual_unit_ids": [],
            "projected_seconds": None,
            "estimator_signature": "",
            "last_rebuild_at": "",
            "last_rebuild_reason": "",
        }
    unit_ids = [int(uid) for uid in data.get("unit_ids", []) if isinstance(uid, int) or str(uid).isdigit()]
    manual_ids = [int(uid) for uid in data.get("manual_unit_ids", []) if isinstance(uid, int) or str(uid).isdigit()]
    return {
        "date": today,
        "daily_minutes": int(data.get("daily_minutes") or daily_minutes),
        "unit_ids": unit_ids,
        "manual_unit_ids": manual_ids,
        "projected_seconds": data.get("projected_seconds"),
        "estimator_signature": str(data.get("estimator_signature") or ""),
        "last_rebuild_at": str(data.get("last_rebuild_at") or ""),
        "last_rebuild_reason": str(data.get("last_rebuild_reason") or ""),
    }


def _store_session_queue_snapshot(
    today: str,
    daily_minutes: int,
    unit_ids: list[int],
    manual_unit_ids: list[int],
    projected_seconds: float | None = None,
    estimator_signature: str = "",
    last_rebuild_at: str = "",
    last_rebuild_reason: str = "",
) -> None:
    _SESSION_QUEUE_SNAPSHOT["date"] = today
    _SESSION_QUEUE_SNAPSHOT["daily_minutes"] = int(daily_minutes)
    _SESSION_QUEUE_SNAPSHOT["unit_ids"] = [int(uid) for uid in unit_ids]
    _SESSION_QUEUE_SNAPSHOT["manual_unit_ids"] = [int(uid) for uid in manual_unit_ids]
    _SESSION_QUEUE_SNAPSHOT["projected_seconds"] = None if projected_seconds is None else float(projected_seconds)
    _SESSION_QUEUE_SNAPSHOT["estimator_signature"] = str(estimator_signature or "")
    _SESSION_QUEUE_SNAPSHOT["last_rebuild_at"] = str(last_rebuild_at or "")
    _SESSION_QUEUE_SNAPSHOT["last_rebuild_reason"] = str(last_rebuild_reason or "")


def _model_signature(model: RuntimeEstimationModel) -> str:
    payload = {
        "base": round(float(model.global_base_minutes_per_page), 5),
        "stages": {k: round(float(v), 4) for k, v in sorted(model.stage_factors.items())},
        "stale": {k: round(float(v), 4) for k, v in sorted(model.stale_bucket_factors.items())},
        "source_count": len(model.source_factors),
        "obs_count": int(model.debug.get("observation_count", 0)),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _build_runtime_model_for_request(review_repo: ReviewRepo, settings_repo: SettingsRepo) -> RuntimeEstimationModel:
    return build_runtime_estimation_model(
        observations=review_repo.runtime_estimation_observations(),
        fallback_seconds_per_page=float(settings_repo.get("fallback_review_seconds_per_page", "60")),
        fallback_seconds_per_unit=float(settings_repo.get("fallback_review_seconds_per_unit", "90")),
    )



def _enable_smooth_scroll(view: QAbstractItemView) -> None:
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.verticalScrollBar().setSingleStep(18)
    view.horizontalScrollBar().setSingleStep(18)




class DocumentProgressBar(QWidget):
    page_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: list[dict] = []
        self._segment_regions: list[tuple[tuple[int, int, int, int], dict]] = []
        self._division_markers: list[dict] = []
        self._division_regions: list[tuple[tuple[int, int, int, int], dict]] = []
        self._total_pages = 1
        self._current_page = 1
        self._hover_key = ""
        self._inner_rect = None
        self.setMinimumHeight(26)
        self.setMouseTracking(True)

    def set_data(self, segments: list[dict], total_pages: int, current_page: int, division_markers: list[dict] | None = None) -> None:
        self._segments = segments
        self._division_markers = division_markers or []
        self._total_pages = max(1, int(total_pages or 1))
        self._current_page = max(1, int(current_page or 1))
        self.update()

    def _segment_tooltip(self, seg: dict) -> str:
        title = seg.get("title", "Unit")
        hierarchy = seg.get("hierarchy_path") or title
        sp = int(seg.get("start_page", 1))
        ep = int(seg.get("end_page", sp))
        state = str(seg.get("state", "unstarted"))
        retention = seg.get("retention")
        retention_text = "n/a" if retention is None else f"{int(round(max(0.01, min(0.99, float(retention))) * 100))}%"
        return f"{title}\nHierarchy: {hierarchy}\nPages: {sp}-{ep}\nState: {state}\nRetention: {retention_text}"

    def _division_tooltip(self, marker: dict) -> str:
        title = marker.get("title", "Division")
        hierarchy = marker.get("hierarchy_path") or title
        page = int(marker.get("page", 1))
        return f"Division: {title}\nHierarchy: {hierarchy}\nStarts at page {page}"

    def _page_for_x(self, x: int) -> int:
        inner = self._inner_rect
        if not inner:
            return self._current_page
        clamped_x = max(inner.left(), min(inner.right(), x))
        ratio = (clamped_x - inner.left()) / max(1, inner.width())
        page = int(round(1 + ratio * (self._total_pages - 1)))
        return max(1, min(self._total_pages, page))

    def mouseMoveEvent(self, event):
        x, y = int(event.position().x()), int(event.position().y())
        for (lx, ty, rx, by), marker in self._division_regions:
            if lx <= x <= rx and ty <= y <= by:
                tip = self._division_tooltip(marker)
                if tip != self._hover_key:
                    self._hover_key = tip
                    self.setToolTip(tip)
                return
        for (lx, ty, rx, by), seg in self._segment_regions:
            if lx <= x <= rx and ty <= y <= by:
                tip = self._segment_tooltip(seg)
                if tip != self._hover_key:
                    self._hover_key = tip
                    self.setToolTip(tip)
                return
        if self._hover_key:
            self._hover_key = ""
            self.setToolTip("")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._inner_rect and self._inner_rect.contains(int(event.position().x()), int(event.position().y())):
            self.page_requested.emit(self._page_for_x(int(event.position().x())))
            return
        super().mousePressEvent(event)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        r = self.rect().adjusted(1, 1, -1, -6)
        p.setPen(QPen(QColor("#24314a"), 1))
        p.setBrush(QColor("#0f1624"))
        p.drawRoundedRect(r, 6, 6)

        inner = r.adjusted(2, 3, -2, -3)
        self._inner_rect = inner
        self._segment_regions = []
        self._division_regions = []
        for seg in self._segments:
            sp = max(1, int(seg.get("start_page", 1)))
            ep = max(sp, int(seg.get("end_page", sp)))
            left = int(inner.left() + ((sp - 1) / self._total_pages) * inner.width())
            right = int(inner.left() + (ep / self._total_pages) * inner.width())
            width = max(2, right - left)
            seg_rect = inner.adjusted(left - inner.left(), 0, -(inner.width() - (left - inner.left()) - width), 0)

            state = seg.get("state", "unstarted")
            glow = QColor(0, 0, 0, 0)
            if state == "mastered":
                c1, c2, glow = QColor("#6bb8d8"), QColor("#6f76c6"), QColor(110, 168, 214, 55)
            elif state == "learning":
                c1, c2, glow = QColor("#6a7f9e"), QColor("#5f9d8d"), QColor(95, 157, 141, 40)
            else:
                c1, c2 = QColor("#434b59"), QColor("#5b6575")

            grad = QLinearGradient(seg_rect.topLeft(), seg_rect.topRight())
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(seg_rect, 2, 2)

            if glow.alpha() > 0:
                p.setBrush(glow)
                p.drawRoundedRect(seg_rect.adjusted(-1, -1, 1, 1), 3, 3)

            depth = max(1, int(seg.get("depth", 3)))
            if depth <= 2:
                tick_h = 9 if depth == 1 else 6
                p.setBrush(QColor("#6f8bb3"))
                p.drawRect(max(seg_rect.left(), inner.left()), inner.bottom() + 1, 1, tick_h)

            self._segment_regions.append(((seg_rect.left(), seg_rect.top(), seg_rect.right(), seg_rect.bottom()), seg))

        for marker in self._division_markers:
            page = max(1, min(self._total_pages, int(marker.get("page", 1))))
            depth = max(1, int(marker.get("depth", 2)))
            x = int(inner.left() + ((page - 1) / self._total_pages) * inner.width())
            tick_h = 10 if depth <= 1 else 7 if depth == 2 else 4
            color = QColor("#7e9cc7") if depth <= 2 else QColor("#5f7395")
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawRect(x, inner.bottom() + 1, 2, tick_h)
            self._division_regions.append(((x - 2, inner.top(), x + 3, inner.bottom() + tick_h + 2), marker))

        marker_x = int(inner.left() + ((self._current_page - 1) / self._total_pages) * inner.width())
        p.setPen(QPen(QColor("#f8fafc"), 2))
        p.drawLine(marker_x, inner.top() - 1, marker_x, inner.bottom() + 2)


class SourcesPage(QWidget):
    queue_changed = Signal()
    library_changed = Signal()
    add_to_today_queue_requested = Signal(int, int)

    def __init__(
        self,
        source_repo: SourceRepo,
        outline_repo: OutlineRepo,
        review_repo: ReviewRepo,
        highlight_repo: HighlightRepo,
        settings_repo: SettingsRepo,
        pdf_service: PdfService,
    ):
        super().__init__()
        self.source_repo = source_repo
        self.outline_repo = outline_repo
        self._review_repo = review_repo
        self._highlight_repo = highlight_repo
        self._settings_repo = settings_repo
        self.pdf_service = pdf_service
        self.current_source_id = None

        search = QLineEdit()
        search.setPlaceholderText("Search sources...")
        search.textChanged.connect(self.refresh)
        self.search = search
        self.list = QListWidget()
        _enable_smooth_scroll(self.list)
        self.list.currentRowChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(self._open_current_workspace)

        left = QVBoxLayout()
        left.addWidget(search)
        left.addWidget(self.list)
        import_btn = QPushButton("Import PDF")
        import_btn.setObjectName("accent")
        import_btn.clicked.connect(self.import_pdf)
        left.addWidget(import_btn)

        self.detail = QLabel("Select a source")
        self.detail.setWordWrap(True)
        btn_open = QPushButton("Open Workspace")
        btn_open.setObjectName("accent")
        btn_open.clicked.connect(self._open_current_workspace)
        btn_source_actions = QPushButton("Source Actions ▾")
        btn_source_actions.clicked.connect(self.open_source_actions_menu)
        btn_delete = QPushButton("Delete Source")
        btn_delete.clicked.connect(self.delete_source)
        btn_delete.setStyleSheet("background:#3a1f24; border:1px solid #5a2a33;")
        right = QVBoxLayout()
        right.addWidget(self.detail)
        for b in [btn_open, btn_source_actions]:
            right.addWidget(b)
        right.addStretch()
        right.addWidget(btn_delete)

        split = QSplitter()
        lw = QWidget(); lw.setLayout(left)
        rw = QWidget(); rw.setLayout(right)
        split.addWidget(lw); split.addWidget(rw)
        split.setSizes([700, 300])
        self.list_view = QWidget()
        list_view_layout = QVBoxLayout(self.list_view)
        list_view_layout.setContentsMargins(0, 0, 0, 0)
        list_view_layout.addWidget(split)

        self.back_btn = QPushButton("← Back to Sources")
        self.back_btn.clicked.connect(self.show_list)
        self.ctx_source = QLabel("Source: -")
        self.ctx_source.setStyleSheet("font-weight:600;")
        self.ctx_path = QLabel("Sources")
        self.ctx_path.setStyleSheet("color:#9aa7b2;")
        self.workspace_host = QWidget()
        self.workspace_host_layout = QVBoxLayout(self.workspace_host)
        self.workspace_host_layout.setContentsMargins(0, 0, 0, 0)

        self.workspace_view = QWidget()
        workspace_layout = QVBoxLayout(self.workspace_view)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        top_bar = QHBoxLayout()
        top_bar.addWidget(self.back_btn)
        top_bar.addWidget(self.ctx_source)
        top_bar.addWidget(self.ctx_path)
        top_bar.addStretch()
        workspace_layout.addLayout(top_bar)
        workspace_layout.addWidget(self.workspace_host, 1)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.workspace_view)

        lay = QVBoxLayout(self)
        lay.addWidget(self.stack)
        self.current_workspace = None
        self.refresh()

    def _open_current_workspace(self, *_):
        if self.current_source_id:
            self.show_workspace(self.current_source_id)

    def show_workspace(self, source_id: int):
        if self.current_workspace is None:
            self.current_workspace = SourceWorkspace(
                source_id,
                self.source_repo,
                self.outline_repo,
                self._review_repo,
                self._highlight_repo,
                self._settings_repo,
                self.pdf_service,
            )
            self.current_workspace.queue_changed.connect(self.queue_changed.emit)
            self.current_workspace.context_changed.connect(self.on_workspace_context_changed)
            self.current_workspace.add_to_today_queue_requested.connect(self.add_to_today_queue_requested.emit)
            self.workspace_host_layout.addWidget(self.current_workspace)
        else:
            self.current_workspace.set_source(source_id)
        self.stack.setCurrentWidget(self.workspace_view)

    def on_workspace_context_changed(self, source_title: str, section_path: str):
        self.ctx_source.setText(f"Source: {source_title}")
        if section_path:
            self.ctx_path.setText(f"Sources / {source_title} / {section_path}")
        else:
            self.ctx_path.setText(f"Sources / {source_title}")

    def show_list(self):
        self.stack.setCurrentWidget(self.list_view)

    def refresh(self):
        q = self.search.text().strip()
        self.sources = self.source_repo.list_sources(q)
        self.list.clear()
        for s in self.sources:
            suffix = "" if s.file_exists else " (missing file)"
            item = QListWidgetItem(f"{s.title} · {s.page_count}p{suffix}")
            item.setData(256, s.id)
            self.list.addItem(item)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)

    def _on_select(self, idx):
        if idx < 0:
            return
        s = self.sources[idx]
        self.current_source_id = s.id
        self.detail.setText(
            f"Title: {s.title}\nActive: {s.is_active}\nOrder: {s.learning_mode}\nFile: {s.file_path}\nExists: {s.file_exists}\nPages: {s.page_count}"
        )

    def import_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        try:
            size, pages = self.pdf_service.inspect(path)
            source_id = self.source_repo.create(Path(path).stem, path, size, pages)
            entries = self.pdf_service.generate_outline_entries(path, Path(path).stem, pages)
            self.outline_repo.replace_outline(source_id, entries)
            self.refresh()
            self.library_changed.emit()
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))

    def edit_source(self):
        if not self.current_source_id:
            return
        s = self.source_repo.get(self.current_source_id)
        dlg = SourceMetadataDialog(s.title, s.is_active, s.learning_mode, self)
        if dlg.exec():
            active = dlg.active_edit.text().strip().lower() in {"y", "yes", "true", "1"}
            learning_mode = "strict" if dlg.learning_mode_edit.text().strip().lower() in {"strict", "ordered", "linear"} else "any"
            self.source_repo.update_metadata(s.id, dlg.title_edit.text().strip() or s.title, active, learning_mode)
            self.refresh()
            self.library_changed.emit()

    def relink_source(self):
        if not self.current_source_id:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Relink PDF", "", "PDF files (*.pdf)")
        if not path:
            return
        size, pages = self.pdf_service.inspect(path)
        self.source_repo.relink(self.current_source_id, path, size, pages)
        self.refresh()
        self.library_changed.emit()

    def toggle_active(self):
        if not self.current_source_id:
            return
        s = self.source_repo.get(self.current_source_id)
        self.source_repo.update_metadata(s.id, s.title, not s.is_active, s.learning_mode)
        self.refresh()
        self.library_changed.emit()

    def delete_source(self):
        if not self.current_source_id:
            return
        s = self.source_repo.get(self.current_source_id)
        if not s:
            return
        ans = QMessageBox.question(
            self,
            "Delete source",
            f"Delete source '{s.title}'? This removes outline, units, highlights, and review history.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return
        self.source_repo.delete(self.current_source_id)
        self.current_source_id = None
        self.refresh()
        self.library_changed.emit()

    def open_source_actions_menu(self):
        if not self.current_source_id:
            return
        menu = QMenu(self)
        edit = menu.addAction("Edit Metadata")
        relink = menu.addAction("Relink File")
        active = menu.addAction("Toggle Active")
        chosen = menu.exec(QCursor.pos())
        if chosen == edit:
            self.edit_source()
        elif chosen == relink:
            self.relink_source()
        elif chosen == active:
            self.toggle_active()


class SourceWorkspace(QWidget):
    queue_changed = Signal()
    context_changed = Signal(str, str)
    add_to_today_queue_requested = Signal(int, int)
    def __init__(
        self,
        source_id: int,
        source_repo: SourceRepo,
        outline_repo: OutlineRepo,
        review_repo: ReviewRepo,
        highlight_repo: HighlightRepo,
        settings_repo: SettingsRepo,
        pdf_service: PdfService,
    ):
        super().__init__()
        self.source_id = source_id
        self.source_repo = source_repo
        self.outline_repo = outline_repo
        self.review_repo = review_repo
        self.highlight_repo = highlight_repo
        self.settings_repo = settings_repo
        self.pdf_service = pdf_service
        self._annotation_palette = [
            ("Blue", "#2d9cdb"),
            ("Purple", "#8b5cf6"),
            ("Green", "#27ae60"),
            ("Yellow", "#f1c40f"),
            ("Red", "#e74c3c"),
        ]
        self._annotation_tool = self.settings_repo.get_ui_state("pdf_annotation_tool", "select_text") or "select_text"
        self._annotation_color = self.settings_repo.get_ui_state("pdf_annotation_color", "#2d9cdb") or "#2d9cdb"
        try:
            self._annotation_opacity = float(self.settings_repo.get_ui_state("pdf_annotation_opacity", "0.35"))
        except Exception:
            self._annotation_opacity = 0.35
        self._annotation_opacity = max(0.0, min(1.0, self._annotation_opacity))
        root = self
        split = QSplitter()

        self.search = QLineEdit(); self.search.setPlaceholderText("Filter outline")
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Outline", "Queue"])
        self.tree.setIndentation(14)
        self.tree.setColumnWidth(0, 250)
        _enable_smooth_scroll(self.tree)
        self.tree.itemSelectionChanged.connect(self.on_item_select)
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.jump_to_selected())
        self.tree.itemExpanded.connect(self.on_item_expanded)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_tree_context_menu)
        btn_tree_actions = QPushButton("Tree Actions ▾")
        btn_tree_actions.clicked.connect(self.open_tree_actions_menu)
        btn_expand = QPushButton("Expand")
        btn_expand.clicked.connect(self.tree.expandAll)
        btn_collapse = QPushButton("Collapse")
        btn_collapse.clicked.connect(self.tree.collapseAll)
        btn_edit = QPushButton("Edit Outline")
        for btn in (btn_tree_actions, btn_expand, btn_collapse, btn_edit):
            btn.setFixedHeight(26)
        btn_edit.clicked.connect(self.edit_outline)
        tree_toolbar = QHBoxLayout()
        tree_toolbar.addWidget(btn_tree_actions)
        tree_toolbar.addWidget(btn_expand)
        tree_toolbar.addWidget(btn_collapse)
        tree_toolbar.addWidget(btn_edit)
        left_l = QVBoxLayout(); left_l.addWidget(self.search); left_l.addLayout(tree_toolbar); left_l.addWidget(self.tree)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self.refresh_tree)
        self.search.textChanged.connect(lambda *_: self._filter_timer.start(220))

        self.pdf = PersistentPdfViewer()
        self.pdf.set_selection_menu_handler(self.open_selection_menu)
        self.pdf.set_area_created_handler(self._on_area_rect_created)
        self.pdf.set_highlight_hit_handler(self._on_overlay_highlight_hit)
        self.zoom = QSpinBox(); self.zoom.setRange(50, 250); self.zoom.setValue(100)
        self.zoom.valueChanged.connect(lambda v: self.pdf.set_zoom(v / 100))
        btn_jump = QPushButton("Jump To Selected Unit")
        btn_jump.setObjectName("accent")
        btn_jump.clicked.connect(self.jump_to_selected)
        btn_unit_actions = QPushButton("Unit Actions ▾")
        btn_unit_actions.clicked.connect(self.open_unit_actions_menu)
        btn_add_today = QPushButton("Add for Today's Queue")
        btn_add_today.setObjectName("accent")
        btn_add_today.clicked.connect(self.add_selected_for_today_queue)
        btn_add_today.setStyleSheet(
            "QPushButton { background:#1f4f3d; border:1px solid #2e7d60; color:#e8fff3; border-radius:6px; padding:6px 10px; }"
            "QPushButton:hover { background:#25664d; }"
        )
        self.btn_add_today = btn_add_today
        self.page_label = QLabel("Page: -")

        self.doc_progress = DocumentProgressBar()
        self.doc_progress.page_requested.connect(self._on_doc_progress_page_requested)

        self._tool_buttons: dict[str, QPushButton] = {}
        self._tool_buttons["select_text"] = self._mk_tool_btn("Select Text", "select_text")
        self._tool_buttons["area"] = self._mk_tool_btn("Area", "area")
        self._tool_buttons["pan"] = self._mk_tool_btn("Pan", "pan")
        self._tool_buttons["erase"] = self._mk_tool_btn("Erase", "erase")

        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Annotate"))
        for btn in self._tool_buttons.values():
            color_row.addWidget(btn)
        color_row.addSpacing(8)
        color_row.addWidget(QLabel("Color"))
        self._color_buttons: dict[str, QPushButton] = {}
        for _label, color in self._annotation_palette:
            cbtn = QPushButton("")
            cbtn.setCheckable(True)
            cbtn.setFixedSize(QSize(18, 18))
            cbtn.clicked.connect(lambda _=False, c=color: self._set_annotation_color(c))
            self._color_buttons[color] = cbtn
            color_row.addWidget(cbtn)
        color_row.addSpacing(8)
        color_row.addWidget(QLabel("Opacity"))
        self.opacity_spin = QSpinBox()
        self.opacity_spin.setRange(10, 100)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setValue(int(round(self._annotation_opacity * 100)))
        self.opacity_spin.valueChanged.connect(self._on_opacity_changed)
        color_row.addWidget(self.opacity_spin)
        self.text_layer_hint = QLabel("Text layer: probing…")
        self.text_layer_hint.setStyleSheet("color:#9aa7b2;")
        color_row.addWidget(self.text_layer_hint)
        color_row.addStretch()

        c_top = QHBoxLayout(); c_top.addWidget(self.zoom); c_top.addWidget(btn_jump); c_top.addWidget(btn_unit_actions); c_top.addStretch()
        pdf_row = QHBoxLayout(); pdf_row.addWidget(self.pdf, 1)
        c_l = QVBoxLayout(); c_l.addLayout(c_top); c_l.addLayout(color_row); c_l.addWidget(self.page_label); c_l.addLayout(pdf_row, 1); c_l.addWidget(self.doc_progress)

        self.insights = QLabel()
        self.insights.setWordWrap(True)
        self.unit_hl_list = QListWidget()
        _enable_smooth_scroll(self.unit_hl_list)
        self.source_hl_tree = QTreeWidget(); self.source_hl_tree.setHeaderLabels(["Page", "Context", "Quote"])
        _enable_smooth_scroll(self.source_hl_tree)
        self.source_hl_tree.itemDoubleClicked.connect(self.on_source_highlight_double_clicked)
        self.source_hl_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.source_hl_tree.customContextMenuRequested.connect(self.open_source_highlight_context_menu)
        self.source_hl_count = QLabel("0 highlights")
        self.source_hl_count.setStyleSheet("color:#9aa7b2;")
        self.source_hl_summary = QLabel("")
        self.source_hl_summary.setWordWrap(True)
        self.source_hl_summary.setStyleSheet("color:#b9c7d8;")
        self.source_filter_type = QComboBox()
        self.source_filter_type.addItems(["All Types", "Text", "Area"])
        self.source_filter_type.currentIndexChanged.connect(lambda *_: self.refresh_highlights())
        self.source_filter_color = QComboBox()
        self.source_filter_color.addItem("All Colors", "")
        for label, color in self._annotation_palette:
            self.source_filter_color.addItem(label, color)
        self.source_filter_color.currentIndexChanged.connect(lambda *_: self.refresh_highlights())
        tabs = QTabWidget()
        tab_ins = QWidget(); l1 = QVBoxLayout(tab_ins); l1.addWidget(self.insights); l1.addStretch()
        tab_unit = QWidget(); l2 = QVBoxLayout(tab_unit); l2.addWidget(self.unit_hl_list)
        self.unit_hl_list.itemDoubleClicked.connect(self.edit_unit_highlight)
        tab_src = QWidget(); l3 = QVBoxLayout(tab_src)
        source_filters = QHBoxLayout()
        source_filters.addWidget(QLabel("Type"))
        source_filters.addWidget(self.source_filter_type)
        source_filters.addWidget(QLabel("Color"))
        source_filters.addWidget(self.source_filter_color)
        source_filters.addStretch()
        source_filters.addWidget(self.source_hl_count)
        l3.addLayout(source_filters)
        l3.addWidget(self.source_hl_summary)
        l3.addWidget(self.source_hl_tree)
        tabs.addTab(tab_ins, "Insights")
        tabs.addTab(tab_unit, "Unit Highlights")
        tabs.addTab(tab_src, "Source Highlights")
        right_l = QVBoxLayout(); right_l.addWidget(btn_add_today); right_l.addWidget(tabs); right_l.addStretch()

        lw = QWidget(); lw.setLayout(left_l)
        cw = QWidget(); cw.setLayout(c_l)
        rw = QWidget(); rw.setLayout(right_l)
        split.addWidget(lw); split.addWidget(cw); split.addWidget(rw)
        split.setSizes([300, 700, 320])
        self.split = split
        lay = QHBoxLayout(root); lay.addWidget(split)

        self._selected_node = None
        self._tree_syncing = False
        self._tree_rows: list = []
        self._rows_by_id: dict[int, dict] = {}
        self._child_ids: dict[int, list[int]] = {}
        self._parent_id: dict[int, int | None] = {}
        self._state_cache: dict[int, Qt.CheckState] = {}
        self._id_to_item: dict[int, QTreeWidgetItem] = {}
        self._node_stats_by_id: dict[int, dict] = {}
        self._active_filter = ""
        self._last_pdf_page = 1
        self._pdf_page_poll = QTimer(self)
        self._pdf_page_poll.timeout.connect(self._on_pdf_page_polled)
        self._pdf_page_poll.start(450)
        self._apply_annotation_ui_state()
        self._btn_today_feedback_timer = QTimer(self)
        self._btn_today_feedback_timer.setSingleShot(True)
        self._btn_today_feedback_timer.timeout.connect(self._update_add_today_button_state)
        self.load_source()

    def _mk_tool_btn(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setFixedHeight(26)
        btn.clicked.connect(lambda _checked=False, k=key: self._set_annotation_tool(k, from_click=True))
        return btn

    def _apply_annotation_ui_state(self) -> None:
        for key, btn in self._tool_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == self._annotation_tool)
            btn.blockSignals(False)
        for color, btn in self._color_buttons.items():
            selected = color.lower() == self._annotation_color.lower()
            border = "2px solid #d7e1f3" if selected else "1px solid #20304f"
            btn.setStyleSheet(f"background:{color}; border-radius:9px; border:{border};")
            btn.blockSignals(True)
            btn.setChecked(selected)
            btn.blockSignals(False)
        self.opacity_spin.blockSignals(True)
        self.opacity_spin.setValue(int(round(self._annotation_opacity * 100)))
        self.opacity_spin.blockSignals(False)
        self.pdf.set_annotation_tool(self._annotation_tool)

    def _persist_annotation_state(self) -> None:
        self.settings_repo.set_ui_state("pdf_annotation_tool", self._annotation_tool)
        self.settings_repo.set_ui_state("pdf_annotation_color", self._annotation_color)
        self.settings_repo.set_ui_state("pdf_annotation_opacity", f"{self._annotation_opacity:.2f}")

    def _set_annotation_tool(self, tool: str, from_click: bool = True) -> None:
        if tool not in {"select_text", "area", "pan", "erase"}:
            return
        self._annotation_tool = tool
        self._apply_annotation_ui_state()
        if from_click:
            self._persist_annotation_state()

    def _set_annotation_color(self, color: str) -> None:
        self._annotation_color = color
        self._apply_annotation_ui_state()
        self._persist_annotation_state()

    def _on_opacity_changed(self, value: int) -> None:
        self._annotation_opacity = max(0.1, min(1.0, float(value) / 100.0))
        self._persist_annotation_state()

    def _on_area_rect_created(self, norm_rect: dict, page: int) -> None:
        if self._annotation_tool != "area":
            return
        self.highlight_repo.add_area_highlight(
            source_id=self.source_id,
            page=page,
            rects=[norm_rect],
            note="",
            color=self._annotation_color,
            opacity=self._annotation_opacity,
        )
        self.refresh_highlights()

    def _on_overlay_highlight_hit(self, highlight_id: int) -> None:
        if self._annotation_tool == "erase" and highlight_id:
            self._remove_highlight(int(highlight_id))

    def _build_text_anchor_payload(self, raw_text: str) -> dict:
        exact = " ".join((raw_text or "").split()).strip()
        if not exact:
            return {"text_exact": "", "text_prefix": "", "text_suffix": ""}
        prefix = exact[:24]
        suffix = exact[-24:] if len(exact) > 24 else exact
        return {"text_exact": exact, "text_prefix": prefix, "text_suffix": suffix}

    def _reanchor_page_for_text_highlight(self, hrow) -> int:
        page = int(hrow["page"] or 1)
        anchor_type = str(hrow["anchor_type"]) if "anchor_type" in hrow.keys() else "text"
        if anchor_type != "text":
            return page
        resolved = self.highlight_repo.resolve_text_anchor_page(
            source_id=self.source_id,
            text_exact=hrow["text_exact"] if "text_exact" in hrow.keys() else hrow["quote_text"],
            quote_text=hrow["quote_text"],
            page_hint=page,
        )
        return int(resolved) if resolved else page

    def _sync_pdf_overlay_highlights(self) -> None:
        page = int(self.pdf.view_state().get("page", 1))
        overlays: list[dict] = []
        text_marker_index = 0
        for h in self.highlight_repo.list_source_highlights(self.source_id):
            anchor_type = str(h["anchor_type"]) if "anchor_type" in h.keys() else "text"
            if int(h["page"]) != page:
                continue
            if anchor_type == "rect":
                rects_raw = h["rects_json"] or "[]"
                try:
                    rects = json.loads(rects_raw)
                except Exception:
                    rects = []
                if not isinstance(rects, list):
                    rects = []
                overlays.append({
                    "id": int(h["id"]),
                    "color": h["color"] or "#2d9cdb",
                    "opacity": float(h["opacity"] or 0.35),
                    "rects": [r for r in rects if isinstance(r, dict)],
                })
                continue

            # Graceful fallback for text anchors when glyph-quad geometry is unavailable.
            marker_y = 0.03 + (text_marker_index * 0.035)
            text_marker_index += 1
            overlays.append({
                "id": int(h["id"]),
                "color": h["color"] or "#2d9cdb",
                "opacity": min(0.9, max(0.2, float(h["opacity"] or 0.35))),
                "rects": [{"x": 0.02, "y": min(0.95, marker_y), "w": 0.22, "h": 0.02}],
            })
        self.pdf.set_overlay_highlights(overlays)

    def _on_doc_progress_page_requested(self, page: int) -> None:
        self.pdf.set_page(int(page))
        self._refresh_doc_progress(int(page))
        self._sync_pdf_overlay_highlights()

    def set_source(self, source_id: int) -> None:
        if int(source_id) == int(self.source_id):
            return
        self.source_id = source_id
        self._selected_node = None
        self.page_label.setText("Page: -")
        self.search.clear()
        self.load_source()

    def load_source(self):
        self.source = self.source_repo.get(self.source_id)
        self.pdf.load_if_needed(self.source.file_path)
        self.pdf.set_fit_mode()
        self._refresh_text_layer_hint()
        self.refresh_tree()
        self.refresh_insights()
        self.refresh_highlights()
        self._last_pdf_page = int(self.pdf.view_state().get("page", 1))
        self._refresh_doc_progress(self._last_pdf_page)
        self._sync_pdf_overlay_highlights()
        if self.source:
            self.context_changed.emit(self.source.title, "")
        self._update_add_today_button_state()

    def _today_iso(self) -> str:
        return self._today_window()["day_key"]

    def _timezone_offset(self) -> str:
        return normalized_gmt_offset(self.settings_repo.get("timezone_gmt_offset", "+00:00"))

    def _today_window(self) -> dict:
        window = day_window_for_offset(self._timezone_offset(), now_utc())
        return {
            "day_key": window.day_key,
            "start_utc_iso": window.utc_start_iso,
            "next_start_utc_iso": window.utc_next_start_iso,
            "offset": window.offset,
        }

    def _load_today_queue_snapshot(self) -> dict:
        today = self._today_iso()
        default_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        return _load_session_queue_snapshot(today, default_minutes)

    def _store_today_queue_snapshot(self, snapshot: dict) -> None:
        today = self._today_iso()
        daily_minutes = int(snapshot.get("daily_minutes", self.settings_repo.get("daily_minutes", "90")))
        _store_session_queue_snapshot(
            today,
            daily_minutes,
            [int(uid) for uid in snapshot.get("unit_ids", [])],
            [int(uid) for uid in snapshot.get("manual_unit_ids", [])],
            projected_seconds=snapshot.get("projected_seconds"),
            estimator_signature=str(snapshot.get("estimator_signature") or ""),
            last_rebuild_at=str(snapshot.get("last_rebuild_at") or ""),
            last_rebuild_reason=str(snapshot.get("last_rebuild_reason") or ""),
        )

    def _selected_unit_for_today_queue(self):
        items = self.tree.selectedItems()
        if not items:
            return None
        page = int(items[0].data(0, 257) or 0)
        if page < 1:
            return None
        return self.review_repo.first_unit_for_source_page(int(self.source_id), page)

    def _update_add_today_button_state(self) -> None:
        unit = self._selected_unit_for_today_queue()
        if not unit:
            self.btn_add_today.setText("Add for Today's Queue")
            self.btn_add_today.setToolTip("Select a unit or section with page range.")
            return
        snapshot = self._load_today_queue_snapshot()
        in_queue = int(unit.unit_id) in {int(uid) for uid in snapshot.get("unit_ids", [])}
        if in_queue:
            self.btn_add_today.setText("In Queue")
            self.btn_add_today.setStyleSheet(
                "QPushButton { background:#2f3f66; border:1px solid #4b628f; color:#ecf2ff; border-radius:6px; padding:6px 10px; }"
                "QPushButton:hover { background:#3a4f7f; }"
            )
            self.btn_add_today.setToolTip("Click to remove from today's queue.")
        else:
            self.btn_add_today.setText("Add for Today's Queue")
            self.btn_add_today.setStyleSheet(
                "QPushButton { background:#1f4f3d; border:1px solid #2e7d60; color:#e8fff3; border-radius:6px; padding:6px 10px; }"
                "QPushButton:hover { background:#25664d; }"
            )
            self.btn_add_today.setToolTip("Click to add selected unit to today's queue.")

    def _toggle_page_in_today_queue(self, page: int, with_feedback: bool = True) -> bool:
        unit = self.review_repo.first_unit_for_source_page(int(self.source_id), int(page))
        if not unit:
            return False
        snapshot = self._load_today_queue_snapshot()
        unit_ids = [int(uid) for uid in snapshot.get("unit_ids", [])]
        manual_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", [])]
        uid = int(unit.unit_id)
        added = uid not in unit_ids
        if added:
            unit_ids.append(uid)
            if uid not in manual_ids:
                manual_ids.append(uid)
        else:
            unit_ids = [i for i in unit_ids if i != uid]
            manual_ids = [i for i in manual_ids if i != uid]
        snapshot["unit_ids"] = unit_ids
        snapshot["manual_unit_ids"] = manual_ids
        self._store_today_queue_snapshot(snapshot)
        self.queue_changed.emit()
        if with_feedback:
            self.btn_add_today.setText("Added ✓" if added else "Removed ✓")
            self._btn_today_feedback_timer.start(1300)
        else:
            self._update_add_today_button_state()
        return True

    def _add_page_to_today_queue(self, page: int) -> bool:
        unit = self.review_repo.first_unit_for_source_page(int(self.source_id), int(page))
        if not unit:
            return False
        snapshot = self._load_today_queue_snapshot()
        uid = int(unit.unit_id)
        if uid in {int(i) for i in snapshot.get("unit_ids", [])}:
            self._update_add_today_button_state()
            return True
        snapshot["unit_ids"] = [int(i) for i in snapshot.get("unit_ids", [])] + [uid]
        manual_ids = [int(i) for i in snapshot.get("manual_unit_ids", [])]
        if uid not in manual_ids:
            manual_ids.append(uid)
        snapshot["manual_unit_ids"] = manual_ids
        self._store_today_queue_snapshot(snapshot)
        self.queue_changed.emit()
        self._update_add_today_button_state()
        return True

    def _refresh_text_layer_hint(self) -> None:
        if not getattr(self, "source", None) or not self.source or not self.source.file_path:
            self.text_layer_hint.setText("Text layer: unknown")
            return
        probe = self.pdf_service.probe_text_layer(self.source.file_path)
        if probe.get("error"):
            self.text_layer_hint.setText("Text layer: probe failed")
            return
        sampled = int(probe.get("sampled_pages", 0))
        text_pages = int(probe.get("text_pages", 0))
        if probe.get("has_text_layer"):
            self.text_layer_hint.setText(f"Text layer: likely yes ({text_pages}/{sampled})")
        else:
            self.text_layer_hint.setText(f"Text layer: likely image-only (0/{sampled})")

    def refresh_tree(self, preserve_view_state: bool = True):
        expanded_ids: set[int] = set()
        selected_id: int | None = None
        if preserve_view_state:
            expanded_ids, selected_id = self._capture_tree_view_state()

        filt = self.search.text().strip().lower()
        rows = self.outline_repo.nodes_for_source(self.source_id)
        self._active_filter = filt
        self._tree_rows = rows
        self._rows_by_id = {int(r["id"]): r for r in rows}
        self._child_ids = {}
        self._parent_id = {}
        queue_by_id: dict[int, bool] = {}
        roots: list[int] = []
        for r in rows:
            rid = int(r["id"])
            queue_by_id[rid] = bool(r["queue_enabled"])
            pid = int(r["parent_id"]) if r["parent_id"] is not None else None
            self._parent_id[rid] = pid
            if pid is None:
                roots.append(rid)
            else:
                self._child_ids.setdefault(pid, []).append(rid)

        self._state_cache = {}
        due_set = {int(u.unit_id) for u in self.review_repo.source_due_units(self.source_id, iso_utc(now_utc()))}
        unit_rows = self.review_repo.source_units(self.source_id)
        self._node_stats_by_id = {}
        for u in unit_rows:
            node_id = int(u["node_id"])
            review_count = int(u["review_count"] or 0)
            retention = retention_estimate(u, now_utc()) if review_count > 0 else None
            self._node_stats_by_id[node_id] = {
                "is_due": int(u["id"]) in due_set,
                "review_count": review_count,
                "retention": retention,
                "last_review_at": u["last_review_at"],
            }

        def _state(node_id: int):
            if node_id in self._state_cache:
                return self._state_cache[node_id]
            kids = self._child_ids.get(node_id, [])
            if not kids:
                self._state_cache[node_id] = Qt.Checked if queue_by_id.get(node_id, False) else Qt.Unchecked
                return self._state_cache[node_id]
            child_states = [_state(k) for k in kids]
            if all(s == Qt.Checked for s in child_states):
                self._state_cache[node_id] = Qt.Checked
            elif all(s == Qt.Unchecked for s in child_states):
                self._state_cache[node_id] = Qt.Unchecked
            else:
                self._state_cache[node_id] = Qt.PartiallyChecked
            return self._state_cache[node_id]

        for rid in self._rows_by_id:
            _state(rid)

        self._tree_syncing = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self._id_to_item = {}
        if filt:
            for r in rows:
                if filt not in r["title"].lower():
                    continue
                self._add_item_from_row(int(r["id"]), None, include_children=True)
            self.tree.expandAll()
        else:
            for rid in roots:
                self._add_item_from_row(rid, None, include_children=False)
        self.tree.blockSignals(False)
        self._tree_syncing = False

        if preserve_view_state and not filt:
            self._restore_tree_view_state(expanded_ids, selected_id)

        self._refresh_doc_progress(int(self.pdf.view_state().get("page", 1)))

    def _add_item_from_row(self, node_id: int, parent_item: QTreeWidgetItem | None, include_children: bool) -> QTreeWidgetItem:
        r = self._rows_by_id.get(node_id)
        if not r:
            return QTreeWidgetItem()
        is_unit = bool(r["is_unit"])
        txt = r["title"]
        if r["start_page"]:
            txt += f" [{r['start_page']}-{r['end_page']}]"
        if is_unit:
            txt += " · unit"
        item = QTreeWidgetItem([txt, "off"])
        item.setData(0, 256, r["id"])
        item.setData(0, 257, r["start_page"])
        item.setData(0, 258, "unit" if is_unit else "container")
        item.setData(0, 259, False)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        item.setCheckState(1, self._state_cache.get(node_id, Qt.Unchecked))
        if is_unit:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        else:
            item.setForeground(0, QBrush(QColor("#9aa7b2")))
        cstate = item.checkState(1)
        status = "on" if cstate == Qt.Checked else "off" if cstate == Qt.Unchecked else "mixed"
        stats = self._node_stats_by_id.get(int(node_id))
        if stats:
            due_txt = "due" if stats["is_due"] else "ok"
            rev_txt = f"r{stats['review_count']}"
            ret = stats.get("retention")
            ret_txt = "new" if ret is None else f"{int(round(max(0.01, min(0.99, ret)) * 100))}%"
            status = f"{status} · {due_txt} · {ret_txt} · {rev_txt}"
            tooltip = (
                f"Due: {'yes' if stats['is_due'] else 'no'}\n"
                f"Retention: {ret_txt}\n"
                f"Review count: {stats['review_count']}\n"
                f"Last reviewed: {stats.get('last_review_at') or 'never'}"
            )
            item.setToolTip(0, tooltip)
            item.setToolTip(1, tooltip)
        item.setText(1, status)
        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)
        self._id_to_item[node_id] = item

        kids = self._child_ids.get(node_id, [])
        if not include_children and kids:
            item.addChild(QTreeWidgetItem(["", ""]))
        elif include_children:
            for kid_id in kids:
                self._add_item_from_row(kid_id, item, include_children=True)
            item.setData(0, 259, True)
        return item

    def on_item_expanded(self, item):
        if self._active_filter:
            return
        self._materialize_item_children(item)

    def _materialize_item_children(self, item: QTreeWidgetItem) -> None:
        if item.data(0, 259):
            return
        node_id = int(item.data(0, 256))
        kids = self._child_ids.get(node_id, [])
        if not kids:
            item.setData(0, 259, True)
            return
        self._tree_syncing = True
        self.tree.blockSignals(True)
        item.takeChildren()
        for kid_id in kids:
            self._add_item_from_row(kid_id, item, include_children=False)
        item.setData(0, 259, True)
        self.tree.blockSignals(False)
        self._tree_syncing = False

    def _ensure_item_loaded(self, node_id: int) -> QTreeWidgetItem | None:
        if node_id in self._id_to_item:
            return self._id_to_item[node_id]

        lineage: list[int] = []
        cursor = node_id
        while cursor not in self._id_to_item:
            lineage.append(cursor)
            parent = self._parent_id.get(cursor)
            if parent is None:
                break
            cursor = parent

        if cursor not in self._id_to_item:
            return None

        for nid in reversed(lineage):
            parent_id = self._parent_id.get(nid)
            if parent_id is None:
                continue
            parent_item = self._id_to_item.get(parent_id)
            if parent_item is None:
                return None
            self.tree.expandItem(parent_item)
            self._materialize_item_children(parent_item)
            if nid not in self._id_to_item:
                return None

        return self._id_to_item.get(node_id)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "split"):
            return
        w = self.width()
        if w < 1250:
            self.split.setSizes([280, max(420, w - 560), 220])
        else:
            self.split.setSizes([300, max(520, w - 660), 320])

    def _capture_tree_view_state(self) -> tuple[set[int], int | None]:
        expanded_ids: set[int] = set()

        def walk(item: QTreeWidgetItem) -> None:
            node_id = item.data(0, 256)
            if node_id is not None and item.isExpanded():
                expanded_ids.add(int(node_id))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

        selected_items = self.tree.selectedItems()
        selected_id = int(selected_items[0].data(0, 256)) if selected_items else None
        return expanded_ids, selected_id

    def _restore_tree_view_state(self, expanded_ids: set[int], selected_id: int | None) -> None:
        if not expanded_ids and selected_id is None:
            return

        sorted_ids = sorted(expanded_ids, key=lambda nid: int(self._rows_by_id.get(nid, {}).get("depth", 0)))
        for node_id in sorted_ids:
            item = self._ensure_item_loaded(node_id)
            if item is not None:
                self.tree.expandItem(item)
                self._materialize_item_children(item)

        if selected_id is not None:
            item = self._ensure_item_loaded(selected_id)
            if item is not None:
                self.tree.setCurrentItem(item)

    def on_item_select(self):
        items = self.tree.selectedItems()
        if not items:
            return
        it = items[0]
        self._selected_node = it.data(0, 256)
        page = it.data(0, 257)
        selected_label = it.text(0).split(" [", 1)[0]
        selected_label = selected_label.replace(" · unit", "")
        if page:
            self.page_label.setText(f"Page: {page}")
        if self.source:
            self.context_changed.emit(self.source.title, selected_label)
        self.refresh_insights()
        self.refresh_highlights()
        self._update_add_today_button_state()

    def _on_pdf_page_polled(self) -> None:
        page = int(self.pdf.view_state().get("page", 1))
        if page == self._last_pdf_page:
            return
        self._last_pdf_page = page
        self.page_label.setText(f"Page: {page}")
        self._refresh_doc_progress(page)
        self._sync_pdf_overlay_highlights()

    def _refresh_doc_progress(self, current_page: int) -> None:
        units = self.review_repo.source_units(self.source_id)
        total_pages = int(self.source.page_count or 1) if getattr(self, "source", None) else 1
        now = now_utc()
        segments: list[dict] = []
        depth_by_node = {int(r["id"]): int(r["depth"]) for r in self._rows_by_id.values()}
        division_markers: list[dict] = []
        seen_division_pages: set[int] = set()
        for row in self._rows_by_id.values():
            depth = int(row["depth"] or 0)
            start_page = int(row["start_page"] or 0)
            if depth < 1 or depth > 2 or start_page < 1 or start_page in seen_division_pages:
                continue
            seen_division_pages.add(start_page)
            division_markers.append({
                "title": row["title"],
                "hierarchy_path": self._hierarchy_path_for_node(int(row["id"])),
                "page": start_page,
                "depth": depth,
            })
        for u in units:
            state = "unstarted"
            ret = None
            if u["review_count"] > 0:
                state = "learning"
                ret = retention_estimate(u, now)
                nr = u["next_review_at"]
                if nr:
                    try:
                        next_dt = parse_iso_to_utc(nr)
                        if ret >= 0.9 and (next_dt - now) >= timedelta(days=180):
                            state = "mastered"
                    except Exception:
                        pass
            segments.append({
                "title": u["title"],
                "start_page": int(u["start_page"]),
                "end_page": int(u["end_page"]),
                "state": state,
                "retention": ret,
                "depth": depth_by_node.get(int(u["node_id"]), 3),
                "hierarchy_path": self._hierarchy_path_for_node(int(u["node_id"])),
            })
        self.doc_progress.set_data(
            segments,
            total_pages=total_pages,
            current_page=current_page,
            division_markers=sorted(division_markers, key=lambda m: (int(m["page"]), int(m["depth"]))),
        )

    def _hierarchy_path_for_node(self, node_id: int) -> str:
        parts: list[str] = []
        cursor: int | None = int(node_id)
        seen: set[int] = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            row = self._rows_by_id.get(cursor)
            if not row:
                break
            parts.append(str(row["title"]))
            cursor = self._parent_id.get(cursor)
        parts.reverse()
        return " > ".join(parts)

    def on_item_changed(self, item, col):
        if col != 1 or self._tree_syncing:
            return
        state = item.checkState(1)
        if state == Qt.PartiallyChecked:
            return
        node_id = int(item.data(0, 256))
        enabled = state == Qt.Checked
        self.outline_repo.set_queue_enabled(node_id, enabled)
        self._sync_state_cache_after_toggle(node_id, enabled)
        self._apply_loaded_visual_states(item, enabled)
        self.queue_changed.emit()

    def _state_text(self, state: Qt.CheckState) -> str:
        if state == Qt.Checked:
            return "on"
        if state == Qt.Unchecked:
            return "off"
        return "mixed"

    def _sync_state_cache_after_toggle(self, node_id: int, enabled: bool) -> None:
        target_state = Qt.Checked if enabled else Qt.Unchecked
        stack = [node_id]
        while stack:
            nid = stack.pop()
            self._state_cache[nid] = target_state
            stack.extend(self._child_ids.get(nid, []))

        cursor = self._parent_id.get(node_id)
        while cursor is not None:
            child_states = [self._state_cache.get(cid, Qt.Unchecked) for cid in self._child_ids.get(cursor, [])]
            if child_states and all(s == Qt.Checked for s in child_states):
                self._state_cache[cursor] = Qt.Checked
            elif child_states and all(s == Qt.Unchecked for s in child_states):
                self._state_cache[cursor] = Qt.Unchecked
            else:
                self._state_cache[cursor] = Qt.PartiallyChecked
            cursor = self._parent_id.get(cursor)

    def _apply_loaded_visual_states(self, item: QTreeWidgetItem, enabled: bool) -> None:
        target_state = Qt.Checked if enabled else Qt.Unchecked

        def apply_subtree(node_item: QTreeWidgetItem) -> None:
            node_item.setCheckState(1, target_state)
            node_item.setText(1, self._state_text(target_state))
            for idx in range(node_item.childCount()):
                apply_subtree(node_item.child(idx))

        self._tree_syncing = True
        self.tree.blockSignals(True)
        apply_subtree(item)

        parent = item.parent()
        while parent is not None:
            child_states = [parent.child(i).checkState(1) for i in range(parent.childCount())]
            if child_states and all(s == Qt.Checked for s in child_states):
                pstate = Qt.Checked
            elif child_states and all(s == Qt.Unchecked for s in child_states):
                pstate = Qt.Unchecked
            else:
                pstate = Qt.PartiallyChecked
            parent.setCheckState(1, pstate)
            parent.setText(1, self._state_text(pstate))
            parent = parent.parent()

        self.tree.blockSignals(False)
        self._tree_syncing = False

    def open_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        node_type = item.data(0, 258)
        node_id = item.data(0, 256)
        if node_type == "container":
            act_on = menu.addAction("Enable Chapter")
            act_off = menu.addAction("Disable Chapter")
            act_on.triggered.connect(lambda: self._set_node_enabled(node_id, True))
            act_off.triggered.connect(lambda: self._set_node_enabled(node_id, False))
        else:
            act_on = menu.addAction("Enable Unit")
            act_off = menu.addAction("Disable Unit")
            act_on.triggered.connect(lambda: self._set_node_enabled(node_id, True))
            act_off.triggered.connect(lambda: self._set_node_enabled(node_id, False))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_node_enabled(self, node_id: int, enabled: bool):
        self.outline_repo.set_queue_enabled(int(node_id), enabled)
        self.refresh_tree()
        self.queue_changed.emit()

    def jump_to_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        page = items[0].data(0, 257)
        if page:
            self.pdf.set_page(page)

    def add_selected_for_today_queue(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            QMessageBox.information(self, "No selection", "Select a unit or section first.")
            return
        page = int(items[0].data(0, 257) or 0)
        if page < 1:
            QMessageBox.information(self, "No page", "Selected node has no page range to map into queue.")
            return
        if not self._toggle_page_in_today_queue(page, with_feedback=True):
            QMessageBox.information(self, "Not added", "Couldn't map this selection to a unit.")

    def edit_outline(self):
        nodes = self.outline_repo.nodes_for_source(self.source_id)
        entries = [{
            "depth": n["depth"], "title": n["title"], "start_page": n["start_page"], "end_page": n["end_page"]
        } for n in nodes]
        dlg = OutlineEditorDialog(entries_to_text(entries), self)
        if dlg.exec() and dlg.parsed_entries is not None:
            self.outline_repo.replace_outline(self.source_id, dlg.parsed_entries)
            self.refresh_tree()
            self.refresh_insights()
            self.queue_changed.emit()

    def _bulk(self, enabled: bool):
        self.outline_repo.bulk_set_source(self.source_id, enabled)
        self.refresh_tree()
        self.queue_changed.emit()

    def open_tree_actions_menu(self):
        menu = QMenu(self)
        en = menu.addAction("Enable All")
        dis = menu.addAction("Disable All")
        chosen = menu.exec(QCursor.pos())
        if chosen == en:
            self._bulk(True)
        elif chosen == dis:
            self._bulk(False)

    def open_unit_actions_menu(self):
        menu = QMenu(self)
        jump = menu.addAction("Jump To Selected Unit")
        hl = menu.addAction("Add Highlight from Clipboard")
        chosen = menu.exec(QCursor.pos())
        if chosen == jump:
            self.jump_to_selected()
        elif chosen == hl:
            self.add_highlight_from_clipboard()

    def refresh_insights(self):
        units = self.review_repo.source_units(self.source_id)
        now = now_utc()
        learned = [u for u in units if int(u["review_count"] or 0) > 0 and u["last_review_at"]]
        low_threshold = float(self.settings_repo.get("min_retention_percent", "45")) / 100.0
        low = sum(1 for u in learned if retention_estimate(u, now) < low_threshold)
        avg = (sum(retention_estimate(u, now) for u in learned) / len(learned)) if learned else None
        avg_text = f"{avg:.0%}" if avg is not None else "n/a"
        due_units = self.review_repo.source_due_units(self.source_id, iso_utc(now))
        backlog_seconds = sum(float(self._estimate_review_seconds_unit_row(u)) for u in due_units)
        seven_day_start = iso_utc(now - timedelta(days=7))
        seven_day_end = iso_utc(now)
        seven_day = self.review_repo.review_summary_between(seven_day_start, seven_day_end, source_id=self.source_id)
        last_review_at = self.review_repo.source_last_review_at(self.source_id) or "never"
        self.insights.setText(
            f"Learned / Total: {len(learned)} / {len(units)}\n"
            f"Due now: {len(due_units)}\n"
            f"Backlog minutes: {backlog_seconds / 60.0:.1f}\n"
            f"Last 7d: {seven_day['review_count']} reviews · {seven_day['total_seconds'] / 60.0:.1f} min\n"
            f"Avg retention (learned): {avg_text}\n"
            f"Low retention (<{int(low_threshold * 100)}%): {low}\n"
            f"Last reviewed: {last_review_at}"
        )

    def _estimate_review_seconds_unit_row(self, unit_row) -> float:
        pages = max(1, (int(unit_row["end_page"]) - int(unit_row["start_page"])) + 1)
        fallback_per_page = float(self.settings_repo.get("fallback_review_seconds_per_page", "60"))
        fallback_per_unit = float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
        if int(unit_row["review_count"] or 0) == 0:
            return max(1.0, pages * fallback_per_page, fallback_per_unit)
        unit_avg = self.review_repo.avg_elapsed_seconds_for_unit(int(unit_row["id"]))
        if unit_avg is not None:
            return float(unit_avg)
        source_avg = self.review_repo.avg_elapsed_seconds_for_source(self.source_id)
        if source_avg is not None:
            return float(source_avg)
        global_avg = self.review_repo.avg_elapsed_seconds_global()
        if global_avg is not None:
            return float(global_avg)
        return max(1.0, pages * fallback_per_page, fallback_per_unit)

    def add_highlight_from_clipboard(self):
        cb = QApplication.clipboard()
        quote = (cb.text() or "").strip()
        if not quote:
            QMessageBox.information(self, "No text", "Copy selected text first (or any text to clipboard), then add highlight.")
            return
        page, ok = QInputDialog.getInt(self, "Highlight Page", "Page number", max(1, int(self.pdf.view_state().get("page", 1))), 1, 100000)
        if not ok:
            return
        note, ok2 = QInputDialog.getText(self, "Optional note", "Note (optional)")
        if not ok2:
            note = ""
        anchor = self._build_text_anchor_payload(quote)
        self.highlight_repo.add_text_highlight(
            self.source_id,
            page,
            quote,
            note,
            self._annotation_color,
            text_prefix=anchor["text_prefix"],
            text_exact=anchor["text_exact"],
            text_suffix=anchor["text_suffix"],
            opacity=self._annotation_opacity,
        )
        self.refresh_highlights()

    def _color_actions(self):
        return self._annotation_palette

    def open_selection_menu(self, global_pos, selected_text: str, page: int) -> None:
        quote = (selected_text or "").strip()
        if not quote:
            return
        menu = QMenu(self)
        add_today_queue = menu.addAction("Add to today's queue")
        add_today_queue.triggered.connect(lambda: self._add_page_to_today_queue(int(page)))
        menu.addSeparator()
        quick_add = menu.addAction(f"Add highlight ({self._annotation_color})")
        quick_add.triggered.connect(lambda: self._create_highlight(page, quote, self._annotation_color))
        add_menu = menu.addMenu("Highlight selection")
        for label, color in self._color_actions():
            act = add_menu.addAction(label)
            act.triggered.connect(lambda _=False, c=color: self._create_highlight(page, quote, c))
        menu.addSeparator()
        existing = self.highlight_repo.find_exact(self.source_id, page, quote)
        if existing:
            remove_act = menu.addAction("Remove matching highlight")
            remove_act.triggered.connect(lambda: self._remove_highlight(int(existing["id"])))
        menu.exec(global_pos)

    def _create_highlight(self, page: int, quote: str, color: str) -> None:
        anchor = self._build_text_anchor_payload(quote)
        self.highlight_repo.add_text_highlight(
            self.source_id,
            page,
            quote,
            "",
            color,
            text_prefix=anchor["text_prefix"],
            text_exact=anchor["text_exact"],
            text_suffix=anchor["text_suffix"],
            opacity=self._annotation_opacity,
        )
        self.refresh_highlights()

    def _remove_highlight(self, highlight_id: int) -> None:
        self.highlight_repo.delete_highlight(highlight_id)
        self.refresh_highlights()

    def open_source_highlight_context_menu(self, pos) -> None:
        item = self.source_hl_tree.itemAt(pos)
        if not item:
            return
        hid = item.data(0, 258)
        if not hid:
            return
        menu = QMenu(self)
        jump = menu.addAction("Jump to Highlight")
        edit_note = menu.addAction("Edit Note")
        recolor_menu = menu.addMenu("Recolor")
        recolor_actions = []
        for label, color in self._annotation_palette:
            recolor_actions.append((recolor_menu.addAction(label), color))
        delete = menu.addAction("Delete")
        chosen = menu.exec(self.source_hl_tree.viewport().mapToGlobal(pos))
        if chosen == jump:
            self._jump_to_highlight_and_focus(int(hid))
            return
        if chosen == edit_note:
            note = item.data(0, 259) or ""
            text, ok = QInputDialog.getMultiLineText(self, "Edit highlight note", "Note", note)
            if ok:
                self.highlight_repo.update_highlight_note(int(hid), text)
                self.refresh_highlights()
            return
        for act, color in recolor_actions:
            if chosen == act:
                self.highlight_repo.update_highlight_color(int(hid), color)
                self.refresh_highlights()
                return
        if chosen == delete:
            self._remove_highlight(int(hid))

    def _jump_to_highlight_and_focus(self, highlight_id: int) -> None:
        hrow = self.highlight_repo.get_highlight(int(highlight_id))
        if not hrow:
            return
        page = self._reanchor_page_for_text_highlight(hrow)
        self.pdf.set_page(int(page))

    def on_source_highlight_double_clicked(self, item, _col):
        hid = item.data(0, 258)
        page = item.data(0, 256)
        if page:
            jump_page = int(page)
            if hid:
                hrow = self.highlight_repo.get_highlight(int(hid))
                if hrow:
                    jump_page = self._reanchor_page_for_text_highlight(hrow)
            self.pdf.set_page(jump_page)
        if hid:
            note = item.data(0, 259) or ""
            text, ok = QInputDialog.getMultiLineText(self, "Edit highlight note", "Note", note)
            if ok:
                self.highlight_repo.update_highlight_note(int(hid), text)
                self.refresh_highlights()

    def edit_unit_highlight(self, item):
        hid = item.data(256)
        page = item.data(257)
        if page:
            jump_page = int(page)
            if hid:
                hrow = self.highlight_repo.get_highlight(int(hid))
                if hrow:
                    jump_page = self._reanchor_page_for_text_highlight(hrow)
            self.pdf.set_page(jump_page)
        if not hid:
            return
        note = item.data(258) or ""
        text, ok = QInputDialog.getMultiLineText(self, "Edit highlight note", "Note", note)
        if ok:
            self.highlight_repo.update_highlight_note(int(hid), text)
            self.refresh_highlights()

    def _selected_unit_id(self) -> int | None:
        items = self.tree.selectedItems()
        if not items:
            return None
        node_id = items[0].data(0, 256)
        row = self.review_repo.db.conn.execute("SELECT id FROM units WHERE node_id=?", (node_id,)).fetchone()
        return int(row["id"]) if row else None

    def refresh_highlights(self):
        unit_id = self._selected_unit_id()
        self.unit_hl_list.clear()
        if unit_id:
            for h in self.highlight_repo.list_unit_highlights(unit_id):
                txt = f"p{h['page']} · {h['quote_text'][:100]}"
                if h['note']:
                    txt += f"\n📝 {h['note'][:90]}"
                item = QListWidgetItem(txt)
                item.setData(256, int(h["id"]))
                item.setData(257, int(h["page"]))
                item.setData(258, h["note"] or "")
                item.setForeground(QBrush(QColor(h["color"] or "#2d9cdb")))
                self.unit_hl_list.addItem(item)

        self.source_hl_tree.clear()
        all_rows = list(self.highlight_repo.list_source_highlights(self.source_id))
        selected_type = self.source_filter_type.currentText()
        selected_color = self.source_filter_color.currentData()

        filtered_rows = []
        for h in all_rows:
            anchor_type = str(h["anchor_type"]) if "anchor_type" in h.keys() else "text"
            if selected_type == "Text" and anchor_type != "text":
                continue
            if selected_type == "Area" and anchor_type != "rect":
                continue
            if selected_color and str(h["color"] or "").lower() != str(selected_color).lower():
                continue
            filtered_rows.append(h)

        self.source_hl_count.setText(f"{len(filtered_rows)} shown / {len(all_rows)} total")
        summary = self.highlight_repo.highlight_summary_for_source(self.source_id)
        pages_total = max(1, int(getattr(self.source, "page_count", 0) or 1))
        density = (float(summary["total_highlights"]) / float(pages_total)) * 100.0
        top_pages_txt = ", ".join(f"p{p} ({c})" for p, c in summary["top_pages"]) if summary["top_pages"] else "n/a"
        top_sections_txt = ", ".join(
            f"{entry['title']} ({entry['count']})"
            for entry in summary["top_sections"]
        ) if summary["top_sections"] else "n/a"
        colors_txt = ", ".join(f"{color or 'default'}:{count}" for color, count in summary["color_distribution"][:4]) if summary["color_distribution"] else "n/a"
        self.source_hl_summary.setText(
            f"Total {summary['total_highlights']} · {density:.1f}/100 pages · "
            f"text {summary['text_count']} / area {summary['area_count']} · "
            f"Top pages: {top_pages_txt} · Top sections: {top_sections_txt} · Colors: {colors_txt}"
        )
        group_nodes: dict[str, QTreeWidgetItem] = {}
        page_nodes: dict[tuple[str, int], QTreeWidgetItem] = {}
        page_counts: dict[tuple[str, int], int] = {}

        for h in filtered_rows:
            ctx = h["unit_title"] if h["unit_title"] else "(no unit)"
            page = int(h["page"])
            anchor_type = "area" if (str(h["anchor_type"]) if "anchor_type" in h.keys() else "text") == "rect" else "text"

            if ctx not in group_nodes:
                group_nodes[ctx] = QTreeWidgetItem(["", f"{ctx}", ""])
                self.source_hl_tree.addTopLevelItem(group_nodes[ctx])

            key = (ctx, page)
            if key not in page_nodes:
                page_nodes[key] = QTreeWidgetItem([str(page), f"Page {page}", ""])
                group_nodes[ctx].addChild(page_nodes[key])
                page_counts[key] = 0

            page_counts[key] += 1
            quote = h["quote_text"][:120] if h["quote_text"] else "(area highlight)"
            if h["note"]:
                quote = f"{quote}   📝 {h['note'][:70]}"
            quote = f"[{anchor_type}] {quote}"
            item = QTreeWidgetItem([str(page), ctx, quote])
            item.setData(0, 256, h["page"])
            item.setData(0, 258, h["id"])
            item.setData(0, 259, h["note"] or "")
            color = QColor(h["color"] or "#2d9cdb")
            item.setForeground(0, QBrush(color))
            item.setForeground(2, QBrush(color))
            page_nodes[key].addChild(item)

        for (ctx, page), node in page_nodes.items():
            node.setText(1, f"Page {page} ({page_counts[(ctx, page)]})")
        for ctx, node in group_nodes.items():
            total = sum(page_counts[(g, p)] for (g, p) in page_counts if g == ctx)
            node.setText(1, f"{ctx} ({total})")
        self.source_hl_tree.expandAll()
        self._sync_pdf_overlay_highlights()


class CornerResizeHandle(QFrame):
    def __init__(self, on_delta, parent=None):
        super().__init__(parent)
        self.on_delta = on_delta
        self.setFixedSize(16, 16)
        self.setCursor(Qt.SizeFDiagCursor)
        self._drag_start = None
        self.setStyleSheet("background:#2b3357; border:1px solid #41558f; border-radius:3px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start = event.globalPosition()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            current = event.globalPosition()
            delta = current - self._drag_start
            self._drag_start = current
            self.on_delta(int(delta.y()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)


class QueueTimerTile(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self.setObjectName("queueTimerTile")
        self.setMinimumSize(170, 170)
        self.setMaximumSize(190, 190)
        self.time_lbl = QLabel("00:00")
        self.time_lbl.setAlignment(Qt.AlignCenter)
        self.time_lbl.setStyleSheet("font-size:28px; font-weight:700; color:#ebf1ff;")
        self.start_btn = QPushButton("▶")
        self.pause_btn = QPushButton("⏸")
        self.reset_btn = QPushButton("↺")
        for b in [self.start_btn, self.pause_btn, self.reset_btn]:
            b.setFixedSize(30, 30)
            b.setToolTip({"▶": "Start", "⏸": "Pause", "↺": "Reset"}[b.text()])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(4)
        lay.addWidget(QLabel("Timer"))
        lay.addWidget(self.time_lbl, 1)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.pause_btn)
        btn_row.addWidget(self.reset_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        self._fx = QTimer(self)
        self._fx.timeout.connect(self._tick_fx)
        self._apply_glow(0.0)

    def _tick_fx(self):
        self._phase = (self._phase + 1) % 60
        strength = (self._phase if self._phase <= 30 else (60 - self._phase)) / 30.0
        self._apply_glow(strength)

    def _apply_glow(self, strength: float):
        alpha = int(28 + strength * 35)
        border = int(85 + strength * 35)
        self.setStyleSheet(
            f"#queueTimerTile {{"
            f"background:rgba(18,27,51,{205 + min(50, alpha)});"
            f"border:1px solid rgba(110,140,200,{border});"
            f"border-radius:10px;"
            f"padding:1px;"
            f"}}"
        )

    def set_running(self, running: bool):
        if running:
            if not self._fx.isActive():
                self._fx.start(85)
        else:
            if self._fx.isActive():
                self._fx.stop()
            self._apply_glow(0.0)


class StudyQueuePage(QWidget):
    def __init__(
        self,
        source_repo: SourceRepo,
        review_repo: ReviewRepo,
        settings_repo: SettingsRepo,
        highlight_repo: HighlightRepo,
        pdf_service: PdfService,
    ):
        super().__init__()
        self.source_repo = source_repo
        self.review_repo = review_repo
        self.settings_repo = settings_repo
        self.highlight_repo = highlight_repo
        self.pdf_service = pdf_service
        self.timer_seconds = 0
        self.timer_running = False
        self.active_unit = None
        self.started_at = None
        self.unit_drafts: dict[int, dict] = {}
        self.source_path_cache: dict[int, str] = {}
        self.display_units = []
        self.units = []
        self._queue_last_pdf_page = 1
        self._queue_recalculation_pending = False
        self._annotation_palette = [
            ("Blue", "#2d9cdb"),
            ("Purple", "#8b5cf6"),
            ("Green", "#27ae60"),
            ("Yellow", "#f1c40f"),
            ("Red", "#e74c3c"),
        ]
        self._annotation_tool = self.settings_repo.get_ui_state("pdf_annotation_tool", "select_text") or "select_text"
        self._annotation_color = self.settings_repo.get_ui_state("pdf_annotation_color", "#2d9cdb") or "#2d9cdb"
        try:
            self._annotation_opacity = float(self.settings_repo.get_ui_state("pdf_annotation_opacity", "0.35"))
        except Exception:
            self._annotation_opacity = 0.35
        self._annotation_opacity = max(0.0, min(1.0, self._annotation_opacity))

        self.list = QListWidget()
        _enable_smooth_scroll(self.list)
        self.list.setSpacing(6)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.viewport().installEventFilter(self)
        self.list.currentRowChanged.connect(self.pick_unit)
        self.list.itemDoubleClicked.connect(lambda *_: self.jump_to_active_unit())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.open_queue_item_context_menu)

        self.queue_banner = QLabel("")
        self.queue_banner.setWordWrap(True)
        self.today_strip = QLabel("")
        self.today_strip.setWordWrap(True)
        self.today_strip.setStyleSheet(
            "border:1px solid #2e3a46; border-radius:8px; padding:6px; color:#d9e3ec; background:#151d2a;"
        )
        self._analytics_cache: dict[str, dict] = {}
        self._analytics_cache_ttl_seconds = 5.0
        self._queue_history_by_unit: dict[int, list[dict]] = {}

        left = QVBoxLayout()
        left.addWidget(QLabel("Queue"))
        left.addWidget(self.today_strip)
        left.addWidget(self.queue_banner)
        left.addWidget(self.list)

        self.title = QLabel("No unit selected")
        self.pre_note_text = ""
        self.post_note_text = ""
        self._selected_rating: str | None = None
        self.hint_markdown_text = ""
        self.hint_last_changed_at: str | None = None
        self._hint_dialog: HintMarkdownDialog | None = None
        self._hint_keep_on_top = True
        self._hint_temporarily_disabled_buttons: list[QPushButton] = []
        self.timer_tile = QueueTimerTile()
        self.timer_tile.start_btn.clicked.connect(self.start_timer)
        self.timer_tile.pause_btn.clicked.connect(self.pause_timer)
        self.timer_tile.reset_btn.clicked.connect(self.reset_timer)
        self.pre_note_btn = QPushButton("Edit Pre-recall Note")
        self.hint_btn = QPushButton("Hint")
        self.post_note_btn = QPushButton("Edit Post-recall Note")
        self.pre_note_btn.clicked.connect(self.edit_pre_note)
        self.hint_btn.clicked.connect(self.edit_hint)
        self.post_note_btn.clicked.connect(self.edit_post_note)
        self.pdf = PersistentPdfViewer()
        self.pdf.set_selection_menu_handler(self.open_queue_selection_menu)
        self.pdf.set_area_created_handler(self._on_queue_area_rect_created)
        self.pdf.set_highlight_hit_handler(self._on_queue_overlay_highlight_hit)
        self.pdf.set_multi_page_mode()
        self.pdf.set_fit_mode()
        self.pdf.setMinimumHeight(760)
        self.queue_outline_tree = QTreeWidget()
        self.queue_outline_tree.setHeaderLabels(["Reading Context"])
        self.queue_outline_tree.setMinimumWidth(320)
        _enable_smooth_scroll(self.queue_outline_tree)
        self.queue_outline_tree.itemClicked.connect(self._on_queue_outline_click)
        self._queue_outline_items: dict[int, QTreeWidgetItem] = {}
        self._queue_outline_rows_by_id: dict[int, dict] = {}
        self._queue_outline_child_ids: dict[int, list[int]] = {}
        self._active_queue_outline_node_id: int | None = None
        self.review_history_list = QListWidget()
        self.review_history_list.setMinimumHeight(86)
        self.review_history_list.setMaximumHeight(130)
        self.review_history_list.setMinimumWidth(270)
        _enable_smooth_scroll(self.review_history_list)
        self.review_history_list.setStyleSheet("QListWidget{font-size:11px;}")
        self.full_history_btn = QPushButton("View Full History")
        self.full_history_btn.clicked.connect(self.open_history)

        ratings = QGridLayout()
        ratings.setSpacing(8)
        self._rating_buttons: dict[str, QPushButton] = {}
        rating_pos = [("Easy", "easy", 0, 0), ("With Effort", "with_effort", 0, 1), ("Hard", "hard", 1, 0), ("Skip", "skip", 1, 1)]
        self._rating_button_base_styles = {
            "easy": "background:#24503f; border:1px solid #2e7257; color:#d5f4e4;",
            "with_effort": "background:#564b2a; border:1px solid #86743a; color:#fff0cc;",
            "hard": "background:#5a3036; border:1px solid #8a4a54; color:#ffdbe0;",
            "skip": "background:#3a435d; border:1px solid #4c5877; color:#dbe4ff;",
        }
        for label, r, row, col in rating_pos:
            b = QPushButton(label)
            b.clicked.connect(lambda _, rr=r: self._on_rating_clicked(rr))
            b.setCheckable(True)
            b.setMinimumHeight(36)
            b.setMinimumWidth(132)
            b.setStyleSheet(self._rating_button_base_styles.get(r, ""))
            self._rating_buttons[r] = b
            ratings.addWidget(b, row, col)

        content = QWidget()
        right = QVBoxLayout(content)
        right.setSpacing(6)
        right.addWidget(self.title)

        saved_h = self.settings_repo.get_ui_state("queue_pdf_height", "760")
        try:
            h = max(320, min(1600, int(saved_h)))
        except Exception:
            h = 760
        self.pdf.setMinimumHeight(h)
        self.pdf.setMaximumHeight(h)

        self.queue_doc_progress = DocumentProgressBar()
        self.queue_doc_progress.page_requested.connect(self._on_queue_doc_progress_page_requested)
        queue_annotation_controls = QVBoxLayout()
        queue_annotation_controls.setSpacing(4)
        queue_ann_row1 = QHBoxLayout()
        queue_ann_row1.setSpacing(6)
        queue_ann_row1.addWidget(QLabel("Annotate"))
        self._queue_tool_buttons: dict[str, QPushButton] = {}
        for label, key in [("Select Text", "select_text"), ("Area", "area"), ("Pan", "pan"), ("Erase", "erase")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _=False, k=key: self._set_queue_annotation_tool(k))
            self._queue_tool_buttons[key] = btn
            queue_ann_row1.addWidget(btn)
        queue_ann_row1.addStretch()
        queue_annotation_controls.addLayout(queue_ann_row1)

        queue_ann_row2 = QHBoxLayout()
        queue_ann_row2.setSpacing(6)
        queue_ann_row2.addWidget(QLabel("Color"))
        self._queue_color_buttons: dict[str, QPushButton] = {}
        for _label, color in self._annotation_palette:
            cbtn = QPushButton("")
            cbtn.setCheckable(True)
            cbtn.setFixedSize(QSize(16, 16))
            cbtn.clicked.connect(lambda _=False, c=color: self._set_queue_annotation_color(c))
            self._queue_color_buttons[color] = cbtn
            queue_ann_row2.addWidget(cbtn)
        queue_ann_row2.addSpacing(6)
        queue_ann_row2.addWidget(QLabel("Opacity"))
        self.queue_opacity_spin = QSpinBox()
        self.queue_opacity_spin.setRange(10, 100)
        self.queue_opacity_spin.setSuffix("%")
        self.queue_opacity_spin.setValue(int(round(self._annotation_opacity * 100)))
        self.queue_opacity_spin.valueChanged.connect(self._on_queue_opacity_changed)
        queue_ann_row2.addWidget(self.queue_opacity_spin)
        queue_ann_row2.addStretch()
        queue_annotation_controls.addLayout(queue_ann_row2)

        self.queue_text_layer_hint = QLabel("Text layer: probing…")
        self.queue_text_layer_hint.setStyleSheet("color:#9aa7b2;")
        queue_annotation_controls.addWidget(self.queue_text_layer_hint)
        self.pre_note_btn.setMinimumHeight(32)
        self.hint_btn.setMinimumHeight(32)
        self.post_note_btn.setMinimumHeight(32)
        self.queue_outline_tree.setMinimumHeight(210)
        self.queue_outline_tree.setMaximumHeight(230)
        self.review_history_list.setMinimumHeight(96)
        self.review_history_list.setMaximumHeight(118)

        left_controls_col = QVBoxLayout()
        left_controls_col.setSpacing(7)

        timer_notes_section = QWidget()
        timer_section_l = QHBoxLayout(timer_notes_section)
        timer_section_l.setContentsMargins(0, 0, 0, 0)
        timer_section_l.setSpacing(8)
        timer_section_l.addWidget(self.timer_tile, 0, Qt.AlignLeft | Qt.AlignTop)
        notes_col = QVBoxLayout()
        notes_col.setSpacing(6)
        notes_col.addWidget(self.pre_note_btn)
        notes_col.addWidget(self.hint_btn)
        notes_col.addWidget(self.post_note_btn)
        notes_col.addStretch()
        timer_section_l.addLayout(notes_col, 1)
        left_controls_col.addWidget(timer_notes_section)

        srs_section = QWidget()
        srs_section_l = QVBoxLayout(srs_section)
        srs_section_l.setContentsMargins(0, 0, 0, 0)
        srs_section_l.addLayout(ratings)
        left_controls_col.addWidget(srs_section)

        annotation_section = QWidget()
        annotation_section_l = QVBoxLayout(annotation_section)
        annotation_section_l.setContentsMargins(0, 0, 0, 0)
        annotation_section_l.setSpacing(4)
        annotation_section_l.addWidget(QLabel("Annotations"))
        annotation_section_l.addLayout(queue_annotation_controls)
        left_controls_col.addWidget(annotation_section)

        outline_section = QWidget()
        outline_section_l = QVBoxLayout(outline_section)
        outline_section_l.setContentsMargins(0, 0, 0, 0)
        outline_section_l.setSpacing(4)
        outline_section_l.addWidget(QLabel("Reading Context"))
        outline_section_l.addWidget(self.queue_outline_tree)
        left_controls_col.addWidget(outline_section)

        history_section = QWidget()
        history_section_l = QVBoxLayout(history_section)
        history_section_l.setContentsMargins(0, 0, 0, 0)
        history_section_l.setSpacing(4)
        history_section_l.addWidget(QLabel("Review History"))
        history_section_l.addWidget(self.review_history_list)
        history_section_l.addWidget(self.full_history_btn)
        left_controls_col.addWidget(history_section)
        left_controls_col.addStretch()

        right_pdf_col = QVBoxLayout()
        right_pdf_col.setSpacing(6)
        right_pdf_col.addWidget(self.pdf, 1)
        right_pdf_col.addWidget(self.queue_doc_progress)

        two_col = QHBoxLayout()
        two_col.setSpacing(10)
        left_col_widget = QWidget()
        left_col_widget.setLayout(left_controls_col)
        left_col_widget.setMinimumWidth(360)
        left_col_widget.setMaximumWidth(360)
        right_col_widget = QWidget()
        right_col_widget.setLayout(right_pdf_col)
        two_col.addWidget(left_col_widget)
        two_col.addWidget(right_col_widget, 1)
        right.addLayout(two_col, 1)

        corner_row = QHBoxLayout()
        corner_row.addStretch()
        self.resize_corner = CornerResizeHandle(self._resize_pdf_by_delta)
        corner_row.addWidget(self.resize_corner)
        right.addLayout(corner_row)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.verticalScrollBar().setSingleStep(18)
        controls_scroll.horizontalScrollBar().setSingleStep(18)
        controls_scroll.setWidget(content)

        split = QSplitter(); lw = QWidget(); lw.setLayout(left)
        split.addWidget(lw); split.addWidget(controls_scroll); split.setSizes([300, 900])
        lay = QVBoxLayout(self); lay.addWidget(split)

        self.qt_timer = QTimer(self)
        self.qt_timer.timeout.connect(self.tick)
        self.qt_timer.start(1000)
        self._apply_queue_annotation_ui_state()
        self._update_rating_buttons_ui()
        self.refresh()

    def _on_rating_clicked(self, rating: str) -> None:
        self.rate(rating)

    def _update_rating_buttons_ui(self) -> None:
        for key, btn in self._rating_buttons.items():
            selected = (key == self._selected_rating)
            btn.blockSignals(True)
            btn.setChecked(selected)
            btn.blockSignals(False)
            extra = "border:2px solid #ecf3ff; font-weight:700;" if selected else ""
            btn.setStyleSheet((self._rating_button_base_styles.get(key, "") + extra).strip())

    def invalidate_session_queue_snapshot(self) -> None:
        daily_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        self._store_today_queue_snapshot([], daily_minutes, manual_unit_ids=[])
        self._invalidate_analytics_cache()

    def show_recalculation_pending(self) -> None:
        if self._queue_recalculation_pending:
            return
        self._queue_recalculation_pending = True
        self.units = []
        self.display_units = []
        self.active_unit = None
        self.list.clear()
        self.queue_banner.setText("Queue recalculation in progress…")
        for _ in range(3):
            item = QListWidgetItem()
            card = QFrame()
            card.setObjectName("queueTile")
            card_l = QVBoxLayout(card)
            lbl = QLabel("Recalculating queue…")
            lbl.setStyleSheet("color:#9aa7b2;")
            card_l.addWidget(lbl)
            item.setSizeHint(QSize(280, 64))
            self.list.addItem(item)
            self.list.setItemWidget(item, card)

    def _today_iso(self) -> str:
        return self._today_window()["day_key"]

    def _timezone_offset(self) -> str:
        return normalized_gmt_offset(self.settings_repo.get("timezone_gmt_offset", "+00:00"))

    def _today_window(self) -> dict:
        window = day_window_for_offset(self._timezone_offset(), now_utc())
        return {
            "day_key": window.day_key,
            "start_utc_iso": window.utc_start_iso,
            "next_start_utc_iso": window.utc_next_start_iso,
            "offset": window.offset,
        }

    def _invalidate_analytics_cache(self) -> None:
        self._analytics_cache.clear()

    def _cache_get(self, key: str) -> dict | None:
        entry = self._analytics_cache.get(key)
        if not entry:
            return None
        age = (datetime.utcnow().timestamp() - float(entry.get("cached_at", 0)))
        if age > self._analytics_cache_ttl_seconds:
            self._analytics_cache.pop(key, None)
            return None
        return entry.get("value")

    def _cache_set(self, key: str, value: dict) -> dict:
        self._analytics_cache[key] = {"cached_at": datetime.utcnow().timestamp(), "value": value}
        return value

    def _load_today_queue_snapshot(self) -> dict:
        today = self._today_iso()
        default_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        return _load_session_queue_snapshot(today, default_minutes)

    def _store_today_queue_snapshot(
        self,
        unit_ids: list[int],
        daily_minutes: int,
        manual_unit_ids: list[int] | None = None,
        projected_seconds: float | None = None,
        estimator_signature: str = "",
        last_rebuild_reason: str = "",
    ) -> None:
        today = self._today_iso()
        _store_session_queue_snapshot(
            today,
            int(daily_minutes),
            [int(uid) for uid in unit_ids],
            [int(uid) for uid in (manual_unit_ids or [])],
            projected_seconds=projected_seconds,
            estimator_signature=estimator_signature,
            last_rebuild_at=iso_utc(now_utc()) if last_rebuild_reason else "",
            last_rebuild_reason=last_rebuild_reason,
        )

    def _build_planned_queue_ids(self, due_units: list, daily_minutes: int, strict_sources: set[int]) -> list[int]:
        due_plan = plan_session_queue(
            due_units,
            daily_minutes,
            self._estimate_review_seconds,
            strict_progression_sources=strict_sources,
            source_id_of=lambda u: int(u.source_id),
        )
        planned_ids = [int(u.unit_id) for u in due_plan.selected_units]
        used_seconds = sum(max(1.0, float(self._estimate_review_seconds(u))) for u in due_plan.selected_units)
        remaining_seconds = max(0.0, (float(daily_minutes) * 60.0) - used_seconds)
        if remaining_seconds <= 0.0:
            return planned_ids

        selected = set(planned_ids)
        for unit in self.review_repo.new_units():
            uid = int(unit.unit_id)
            if uid in selected:
                continue
            estimated = max(1.0, float(self._estimate_review_seconds(unit)))
            if estimated > remaining_seconds:
                continue
            planned_ids.append(uid)
            selected.add(uid)
            remaining_seconds -= estimated
            if remaining_seconds <= 0.0:
                break
        return planned_ids

    def _projected_seconds_for_ids(self, unit_ids: list[int]) -> float:
        units = self.review_repo.unit_views_by_ids([int(uid) for uid in unit_ids])
        return float(sum(max(1.0, float(self._estimate_review_seconds(u))) for u in units))

    def _runtime_model_signature(self) -> str:
        model = getattr(self, "_runtime_estimation_model", None)
        if model is None:
            model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
            self._runtime_estimation_model = model
        return _model_signature(model)

    def _resolve_today_queue_ids(self, due_units: list, daily_minutes: int, strict_sources: set[int]) -> tuple[list[int], bool]:
        snapshot = self._load_today_queue_snapshot()
        planned_ids = list(snapshot["unit_ids"])
        due_ids_in_order = [int(u.unit_id) for u in due_units]
        today_window = self._today_window()
        reviewed_today_ordered = self.review_repo.reviewed_unit_ids_between_ordered(
            today_window["start_utc_iso"],
            today_window["next_start_utc_iso"],
        )
        if planned_ids:
            saved_minutes = snapshot.get("daily_minutes")
            reviewed_today = self.review_repo.review_summary_between(
                today_window["start_utc_iso"],
                today_window["next_start_utc_iso"],
            )["review_count"]
            if saved_minutes is not None and saved_minutes != int(daily_minutes) and reviewed_today == 0:
                planned_ids = []
            else:
                due_set = set(due_ids_in_order)
                manual_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", [])]
                manual_set = set(manual_ids)
                stale_auto_ids = [int(uid) for uid in planned_ids if int(uid) not in due_set and int(uid) not in manual_set]
                if stale_auto_ids:
                    planned_ids = []
                else:
                    extra_ids = [int(uid) for uid in manual_ids if int(uid) not in due_set]
                    reordered_ids = due_ids_in_order + [uid for uid in extra_ids if uid not in due_set]
                    if reordered_ids != planned_ids:
                        planned_ids = reordered_ids
                        manual_ids = [int(uid) for uid in manual_ids if int(uid) in planned_ids]
                        self._store_today_queue_snapshot(
                            planned_ids,
                            daily_minutes,
                            manual_unit_ids=manual_ids,
                            projected_seconds=self._projected_seconds_for_ids(planned_ids),
                            estimator_signature=self._runtime_model_signature(),
                            last_rebuild_reason="due_reorder",
                        )
                        return planned_ids, True
                    reviewed_today = self.review_repo.review_count_on_date(self._today_iso())
                    projected_seconds = self._projected_seconds_for_ids(planned_ids)
                    drift_rebuild = should_rebuild_for_estimate_drift(
                        previous_seconds=snapshot.get("projected_seconds"),
                        current_seconds=projected_seconds,
                        unit_count=len(planned_ids),
                        reviewed_today=reviewed_today,
                        last_rebuild_at=str(snapshot.get("last_rebuild_at") or ""),
                        now=now_utc(),
                    )
                    if drift_rebuild:
                        planned_ids = []
        if not planned_ids:
            planned_ids = self._build_planned_queue_ids(due_units, daily_minutes, strict_sources)
            for reviewed_id in reviewed_today_ordered:
                if int(reviewed_id) not in planned_ids:
                    planned_ids.append(int(reviewed_id))
            self._store_today_queue_snapshot(
                planned_ids,
                daily_minutes,
                manual_unit_ids=[],
                projected_seconds=self._projected_seconds_for_ids(planned_ids),
                estimator_signature=self._runtime_model_signature(),
                last_rebuild_reason="planned_build",
            )
            return planned_ids, True
        appended_reviewed = False
        for reviewed_id in reviewed_today_ordered:
            rid = int(reviewed_id)
            if rid not in planned_ids:
                planned_ids.append(rid)
                appended_reviewed = True
        if appended_reviewed:
            manual_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", []) if int(uid) in planned_ids]
            self._store_today_queue_snapshot(
                planned_ids,
                daily_minutes,
                manual_unit_ids=manual_ids,
                projected_seconds=self._projected_seconds_for_ids(planned_ids),
                estimator_signature=self._runtime_model_signature(),
                last_rebuild_reason="include_done_today",
            )
            return planned_ids, True
        if snapshot.get("projected_seconds") is None or not snapshot.get("estimator_signature"):
            manual_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", []) if int(uid) in planned_ids]
            self._store_today_queue_snapshot(
                planned_ids,
                daily_minutes,
                manual_unit_ids=manual_ids,
                projected_seconds=self._projected_seconds_for_ids(planned_ids),
                estimator_signature=self._runtime_model_signature(),
                last_rebuild_reason="snapshot_metadata_sync",
            )
        return planned_ids, False

    def add_unit_to_today_queue(self, source_id: int, page: int) -> bool:
        unit = self.review_repo.first_unit_for_source_page(int(source_id), int(page))
        if not unit:
            return False
        snapshot = self._load_today_queue_snapshot()
        unit_ids = list(snapshot["unit_ids"])
        manual_unit_ids = list(snapshot.get("manual_unit_ids", []))
        if int(unit.unit_id) in unit_ids:
            return False
        unit_ids.append(int(unit.unit_id))
        if int(unit.unit_id) not in manual_unit_ids:
            manual_unit_ids.append(int(unit.unit_id))
        daily_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        self._store_today_queue_snapshot(unit_ids, daily_minutes, manual_unit_ids=manual_unit_ids)
        self._invalidate_analytics_cache()
        self.refresh()
        return True

    def remove_unit_from_today_queue(self, unit_id: int) -> bool:
        snapshot = self._load_today_queue_snapshot()
        unit_ids = [int(uid) for uid in snapshot.get("unit_ids", [])]
        if int(unit_id) not in unit_ids:
            return False
        manual_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", [])]
        unit_ids = [uid for uid in unit_ids if uid != int(unit_id)]
        manual_ids = [uid for uid in manual_ids if uid != int(unit_id)]
        daily_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        self._store_today_queue_snapshot(unit_ids, daily_minutes, manual_unit_ids=manual_ids)
        self._invalidate_analytics_cache()
        self.refresh()
        return True

    def open_queue_item_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        row = self.list.row(item)
        idx = self._display_index_for_row(row)
        if idx < 0 or idx >= len(self.display_units):
            return
        unit, _reason = self.display_units[idx]
        menu = QMenu(self)
        jump = menu.addAction("Open in Reader")
        remove = menu.addAction("Remove from Today's Queue")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen == jump:
            self.list.setCurrentRow(row)
            self.jump_to_active_unit()
        elif chosen == remove:
            self.remove_unit_from_today_queue(int(unit.unit_id))

    def _resize_pdf_by_delta(self, delta: int):
        current = self.pdf.minimumHeight()
        new_h = max(320, min(1600, current + delta))
        self.pdf.setMinimumHeight(new_h)
        self.pdf.setMaximumHeight(new_h)
        self.settings_repo.set_ui_state("queue_pdf_height", str(new_h))

    def _on_queue_doc_progress_page_requested(self, page: int) -> None:
        if not self.active_unit:
            return
        self.pdf.set_page(int(page))
        self._queue_last_pdf_page = int(page)
        self._set_active_queue_outline_by_page(int(page))
        self._refresh_queue_doc_progress(int(page))
        self._sync_queue_pdf_overlays()

    def _on_queue_outline_click(self, item, _col) -> None:
        page = int(item.data(0, 257) or 0)
        if page < 1:
            return
        self.pdf.set_page(page)
        self._queue_last_pdf_page = page
        self._set_active_queue_outline_by_page(page)
        self._refresh_queue_doc_progress(page)
        self._sync_queue_pdf_overlays()

    def _persist_queue_annotation_state(self) -> None:
        self.settings_repo.set_ui_state("pdf_annotation_tool", self._annotation_tool)
        self.settings_repo.set_ui_state("pdf_annotation_color", self._annotation_color)
        self.settings_repo.set_ui_state("pdf_annotation_opacity", f"{self._annotation_opacity:.2f}")

    def _apply_queue_annotation_ui_state(self) -> None:
        for key, btn in self._queue_tool_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(key == self._annotation_tool)
            btn.blockSignals(False)
        for color, btn in self._queue_color_buttons.items():
            selected = color.lower() == self._annotation_color.lower()
            border = "2px solid #d7e1f3" if selected else "1px solid #20304f"
            btn.setStyleSheet(f"background:{color}; border-radius:8px; border:{border};")
            btn.blockSignals(True)
            btn.setChecked(selected)
            btn.blockSignals(False)
        self.queue_opacity_spin.blockSignals(True)
        self.queue_opacity_spin.setValue(int(round(self._annotation_opacity * 100)))
        self.queue_opacity_spin.blockSignals(False)
        self.pdf.set_annotation_tool(self._annotation_tool)

    def _set_queue_annotation_tool(self, tool: str) -> None:
        if tool not in {"select_text", "area", "pan", "erase"}:
            return
        self._annotation_tool = tool
        self._apply_queue_annotation_ui_state()
        self._persist_queue_annotation_state()

    def _set_queue_annotation_color(self, color: str) -> None:
        self._annotation_color = color
        self._apply_queue_annotation_ui_state()
        self._persist_queue_annotation_state()

    def _on_queue_opacity_changed(self, value: int) -> None:
        self._annotation_opacity = max(0.1, min(1.0, float(value) / 100.0))
        self._persist_queue_annotation_state()
        self._sync_queue_pdf_overlays()

    def _queue_text_anchor_payload(self, raw_text: str) -> dict:
        exact = " ".join((raw_text or "").split()).strip()
        if not exact:
            return {"text_exact": "", "text_prefix": "", "text_suffix": ""}
        return {"text_exact": exact, "text_prefix": exact[:24], "text_suffix": exact[-24:] if len(exact) > 24 else exact}

    def _active_source_id(self) -> int | None:
        if not self.active_unit:
            return None
        return int(self.active_unit.source_id)

    def _refresh_queue_text_layer_hint(self, file_path: str) -> None:
        if not file_path:
            self.queue_text_layer_hint.setText("Text layer: unknown")
            return
        probe = self.pdf_service.probe_text_layer(file_path)
        if probe.get("error"):
            self.queue_text_layer_hint.setText("Text layer: probe failed")
            return
        sampled = int(probe.get("sampled_pages", 0))
        text_pages = int(probe.get("text_pages", 0))
        if probe.get("has_text_layer"):
            self.queue_text_layer_hint.setText(f"Text layer: likely yes ({text_pages}/{sampled})")
        else:
            self.queue_text_layer_hint.setText(f"Text layer: likely image-only (0/{sampled})")

    def open_queue_selection_menu(self, global_pos, selected_text: str, page: int) -> None:
        source_id = self._active_source_id()
        quote = (selected_text or "").strip()
        if not source_id or not quote:
            return
        anchor = self._queue_text_anchor_payload(quote)
        menu = QMenu(self)
        quick = menu.addAction(f"Add highlight ({self._annotation_color})")
        quick.triggered.connect(
            lambda: self.highlight_repo.add_text_highlight(
                source_id,
                page,
                quote,
                "",
                self._annotation_color,
                text_prefix=anchor["text_prefix"],
                text_exact=anchor["text_exact"],
                text_suffix=anchor["text_suffix"],
                opacity=self._annotation_opacity,
            )
        )
        menu.addSeparator()
        existing = self.highlight_repo.find_exact(source_id, page, quote)
        if existing:
            remove = menu.addAction("Remove matching highlight")
            remove.triggered.connect(lambda: self.highlight_repo.delete_highlight(int(existing["id"])))
        menu.exec(global_pos)
        self._sync_queue_pdf_overlays()

    def _on_queue_area_rect_created(self, norm_rect: dict, page: int) -> None:
        source_id = self._active_source_id()
        if not source_id or self._annotation_tool != "area":
            return
        self.highlight_repo.add_area_highlight(
            source_id=source_id,
            page=page,
            rects=[norm_rect],
            note="",
            color=self._annotation_color,
            opacity=self._annotation_opacity,
        )
        self._sync_queue_pdf_overlays()

    def _on_queue_overlay_highlight_hit(self, highlight_id: int) -> None:
        if self._annotation_tool == "erase" and highlight_id:
            self.highlight_repo.delete_highlight(int(highlight_id))
            self._sync_queue_pdf_overlays()

    def _sync_queue_pdf_overlays(self) -> None:
        source_id = self._active_source_id()
        if not source_id:
            self.pdf.set_overlay_highlights([])
            return
        page = int(self.pdf.view_state().get("page", 1))
        overlays: list[dict] = []
        text_marker_index = 0
        for h in self.highlight_repo.list_source_highlights(source_id):
            if int(h["page"]) != page:
                continue
            anchor_type = str(h["anchor_type"]) if "anchor_type" in h.keys() else "text"
            if anchor_type == "rect":
                try:
                    rects = json.loads(h["rects_json"] or "[]")
                except Exception:
                    rects = []
                overlays.append({
                    "id": int(h["id"]),
                    "color": h["color"] or "#2d9cdb",
                    "opacity": float(h["opacity"] or 0.35),
                    "rects": [r for r in rects if isinstance(r, dict)],
                })
                continue
            marker_y = 0.03 + (text_marker_index * 0.035)
            text_marker_index += 1
            overlays.append({
                "id": int(h["id"]),
                "color": h["color"] or "#2d9cdb",
                "opacity": min(0.9, max(0.2, float(h["opacity"] or 0.35))),
                "rects": [{"x": 0.02, "y": min(0.95, marker_y), "w": 0.22, "h": 0.02}],
            })
        self.pdf.set_overlay_highlights(overlays)

    def _refresh_queue_outline_tree(self, source_id: int) -> None:
        rows = self.review_repo.db.conn.execute(
            """SELECT id,parent_id,title,depth,order_index,start_page,end_page
            FROM outline_nodes
            WHERE source_id=?
            ORDER BY order_index""",
            (source_id,),
        ).fetchall()
        self.queue_outline_tree.clear()
        self._queue_outline_items = {}
        self._queue_outline_rows_by_id = {int(r["id"]): r for r in rows}
        self._queue_outline_child_ids = {}
        self._active_queue_outline_node_id = None
        for row in rows:
            rid = int(row["id"])
            parent_id = int(row["parent_id"]) if row["parent_id"] is not None else None
            if parent_id is not None:
                self._queue_outline_child_ids.setdefault(parent_id, []).append(rid)

        roots = [rid for rid, row in self._queue_outline_rows_by_id.items() if row["parent_id"] is None]

        def add_node(node_id: int, parent_item: QTreeWidgetItem | None) -> None:
            row = self._queue_outline_rows_by_id.get(node_id)
            if not row:
                return
            label = str(row["title"])
            sp = int(row["start_page"] or 0)
            ep = int(row["end_page"] or sp)
            if sp > 0:
                label += f" [p{sp}-{ep}]"
            item = QTreeWidgetItem([label])
            item.setData(0, 256, int(row["id"]))
            item.setData(0, 257, sp)
            if parent_item is None:
                self.queue_outline_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            self._queue_outline_items[int(row["id"])] = item
            for child_id in self._queue_outline_child_ids.get(int(row["id"]), []):
                add_node(child_id, item)

        for root_id in roots:
            add_node(root_id, None)
        self.queue_outline_tree.expandToDepth(1)

    def _set_active_queue_outline_by_page(self, page: int) -> None:
        if not self._queue_outline_rows_by_id:
            return
        matches = []
        for row in self._queue_outline_rows_by_id.values():
            start_page = int(row["start_page"] or 0)
            end_page = int(row["end_page"] or start_page)
            if start_page < 1:
                continue
            if start_page <= page <= end_page:
                matches.append(row)
        if not matches:
            return
        current = max(matches, key=lambda r: int(r["depth"] or 0))
        current_id = int(current["id"])

        if self._active_queue_outline_node_id in self._queue_outline_items:
            prev_item = self._queue_outline_items[self._active_queue_outline_node_id]
            prev_item.setBackground(0, QBrush())
            f = prev_item.font(0)
            f.setBold(False)
            prev_item.setFont(0, f)

        active_item = self._queue_outline_items.get(current_id)
        if not active_item:
            return
        active_item.setBackground(0, QBrush(QColor("#1a2d4a")))
        f = active_item.font(0)
        f.setBold(True)
        active_item.setFont(0, f)
        self._active_queue_outline_node_id = current_id
        self.queue_outline_tree.setCurrentItem(active_item)

    def _save_current_draft(self):
        if not self.active_unit:
            return
        state = self.pdf.view_state()
        self.unit_drafts[self.active_unit.unit_id] = {
            "pre": self.pre_note_text,
            "post": self.post_note_text,
            "timer_seconds": self.timer_seconds,
            "timer_running": self.timer_running,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "pdf_page": state["page"],
            "pdf_location": state["location"],
            "pdf_zoom": self.pdf.zoom_factor(),
            "selected_rating": self._selected_rating or "",
        }
        self.settings_repo.set_ui_state("queue_pdf_zoom", str(self.pdf.zoom_factor()))

    def _load_draft_for_active(self):
        self.pre_note_text = ""
        self.post_note_text = ""
        self.timer_seconds = 0
        self.timer_running = False
        self.started_at = None
        self._selected_rating = None
        self.timer_tile.time_lbl.setText("00:00")
        self.timer_tile.set_running(False)
        self._refresh_note_previews()
        self._update_rating_buttons_ui()
        if not self.active_unit:
            return
        draft = self.unit_drafts.get(self.active_unit.unit_id)
        if not draft:
            return
        self.pre_note_text = draft.get("pre", "")
        self.post_note_text = draft.get("post", "")
        self.timer_seconds = int(draft.get("timer_seconds", 0))
        self.timer_running = bool(draft.get("timer_running", False))
        started = draft.get("started_at", "")
        if started:
            try:
                self.started_at = parse_iso_to_utc(started)
            except Exception:
                self.started_at = None
        self.timer_tile.time_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")
        self.timer_tile.set_running(self.timer_running)
        self._selected_rating = str(draft.get("selected_rating", "") or "") or None
        self._update_rating_buttons_ui()
        self._refresh_note_previews()
        zoom = float(draft.get("pdf_zoom", self.settings_repo.get_ui_state("queue_pdf_zoom", "1.0") or "1.0"))
        self.pdf.set_zoom(max(0.25, min(4.0, zoom)))
        self.pdf.set_page(int(draft.get("pdf_page", self.active_unit.start_page)), tuple(draft.get("pdf_location", (0, 0))))

    def refresh(self):
        self._queue_recalculation_pending = False
        self._runtime_estimation_model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
        due_units = self.review_repo.due_units(iso_utc(now_utc()))
        source_modes = {s.id: s.learning_mode for s in self.source_repo.list_sources()}
        strict_sources = {sid for sid, mode in source_modes.items() if mode == "strict"}
        available_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        planned_ids, regenerated = self._resolve_today_queue_ids(due_units, available_minutes, strict_sources)
        snapshot = self._load_today_queue_snapshot()
        manual_unit_ids = [int(uid) for uid in snapshot.get("manual_unit_ids", [])]
        today_window = self._today_window()
        completed_today = self.review_repo.reviewed_unit_ids_between(
            today_window["start_utc_iso"],
            today_window["next_start_utc_iso"],
        )
        done_elapsed_by_unit = self.review_repo.review_seconds_by_unit_between(
            today_window["start_utc_iso"],
            today_window["next_start_utc_iso"],
        )
        self.units = self.review_repo.unit_views_by_ids(planned_ids)
        self._queue_history_by_unit = self.review_repo.review_history_for_units([int(u.unit_id) for u in self.units])
        for unit in self.units:
            history = self._queue_history_by_unit.get(int(unit.unit_id), [])
            unit.review_count = len(history)
            unit.last_review_at = str(history[-1]["ended_at"]) if history else None
        todo_units = [u for u in self.units if int(u.unit_id) not in completed_today]
        done_units = [u for u in self.units if int(u.unit_id) in completed_today]
        ordered_units = todo_units + done_units
        self.list.clear()
        self.source_path_cache = {}
        self.display_units = []
        self._queue_last_pdf_page = 1
        total_planned = len(planned_ids)
        completed_count = len(done_units)
        due_count = sum(1 for u in todo_units if int(u.review_count or 0) > 0)
        new_count = max(0, len(todo_units) - due_count)
        regenerated_text = " · rebuilt for today" if regenerated else ""
        self.queue_banner.setText(
            f"Today's fixed queue: {len(todo_units)} to do + {completed_count} done / {total_planned} planned"
            + (f" · {completed_count} done" if completed_count else "")
            + (f" · {due_count} review" if due_count else "")
            + (f" · {new_count} new" if new_count else "")
            + (f" · {len(manual_unit_ids)} manual" if manual_unit_ids else "")
            + regenerated_text
        )
        self._refresh_today_strip(
            total_planned=total_planned,
            planned_ids=planned_ids,
            queue_signature="-".join(str(uid) for uid in planned_ids[:120]),
        )

        delimiter_added = False
        for u in ordered_units:
            is_done = int(u.unit_id) in completed_today
            if is_done and not delimiter_added and todo_units:
                self._add_done_delimiter_row()
                delimiter_added = True
            est_seconds = self._estimate_review_seconds(u)
            retention = self._estimate_retention(u)
            reason = self._queue_reason_for_unit(u, retention, int(u.unit_id) in manual_unit_ids)
            tile = self._build_queue_tile(
                u,
                est_seconds,
                retention,
                progression_reason=reason,
                is_done=is_done,
                actual_seconds=done_elapsed_by_unit.get(int(u.unit_id)),
            )
            item = QListWidgetItem()
            item.setData(256, len(self.display_units))
            self.list.addItem(item)
            self.list.setItemWidget(item, tile)
            self.display_units.append((u, reason))
            if u.source_id not in self.source_path_cache:
                s = self.source_repo.get(u.source_id)
                if s:
                    self.source_path_cache[u.source_id] = s.file_path
                    self.pdf.prime_path(s.file_path)

        self._relayout_queue_tiles()
        first_selectable_row = next((row for row in range(self.list.count()) if self._display_index_for_row(row) >= 0), -1)
        if first_selectable_row >= 0:
            self.list.setCurrentRow(first_selectable_row)
            self._refresh_queue_doc_progress()
        else:
            self.active_unit = None
            self.title.setText("No unit selected")
            self.queue_doc_progress.set_data([], total_pages=1, current_page=1)
            self.pdf.set_overlay_highlights([])

    def eventFilter(self, obj, event):
        if obj is self.list.viewport() and event.type() == QEvent.Resize:
            self._relayout_queue_tiles()
        return super().eventFilter(obj, event)

    def _relayout_queue_tiles(self):
        tile_w = max(220, self.list.viewport().width() - 14)
        for i in range(self.list.count()):
            item = self.list.item(i)
            tile = self.list.itemWidget(item)
            if not tile:
                continue
            if isinstance(tile, QFrame) and tile.objectName() != "queueTile":
                continue
            tile.setFixedWidth(tile_w)
            tile.adjustSize()
            item.setSizeHint(self._queue_tile_size_hint(tile, tile_w))

    def _estimate_review_seconds(self, unit) -> float:
        model = getattr(self, "_runtime_estimation_model", None)
        if model is None:
            model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
            self._runtime_estimation_model = model
        return model.estimate_unit_seconds(unit, now_utc())

    def _estimate_retention(self, unit) -> float | None:
        history = self._queue_history_by_unit.get(int(unit.unit_id), [])
        if not history:
            return None
        now = now_utc()
        try:
            last_review_at = parse_iso_to_utc(str(history[-1]["ended_at"]))
        except Exception:
            return None
        due_iso = _derive_due_at_from_events(history)
        interval_days = 1.0
        if due_iso:
            try:
                due_at = parse_iso_to_utc(str(due_iso))
                interval_days = max(0.3, (due_at - last_review_at).total_seconds() / 86400.0)
            except Exception:
                interval_days = 1.0
        elapsed_days = max(0.0, (now - last_review_at).total_seconds() / 86400.0)
        score = math.exp(-elapsed_days / max(0.3, interval_days))
        return max(0.01, min(0.99, float(score)))

    def _queue_reason_for_unit(self, unit, retention: float | None, is_manual: bool) -> str | None:
        if is_manual:
            return "manual"
        threshold = float(self.settings_repo.get("min_retention_percent", "45")) / 100.0
        if retention is not None and retention < threshold:
            return "low_retention"
        return None

    def _queue_tile_size_hint(self, tile: QWidget, width: int) -> QSize:
        min_h = 110
        max_h = 360
        fallback_h = 136
        try:
            size = tile.sizeHint()
            height = max(min_h, min(max_h, int(size.height()) + 8))
            return QSize(max(220, width), height)
        except Exception:
            return QSize(max(220, width), fallback_h)

    def _apply_queue_tile_style(self, tile: QFrame, is_done: bool, glow_strength: float = 0.0) -> None:
        t = max(0.0, min(1.0, float(glow_strength)))
        if is_done:
            base_border = (94, 116, 160)
            glow_border = (193, 165, 96)
        else:
            base_border = (46, 58, 70)
            glow_border = (136, 170, 230)
        border = tuple(int(round((1.0 - t) * base_border[i] + t * glow_border[i])) for i in range(3))
        tile.setStyleSheet(
            f"#queueTile {{ border: 1px solid rgb({border[0]}, {border[1]}, {border[2]}); border-radius: 10px; padding: 8px; }}"
            "QLabel#tileTitle { font-size: 15px; font-weight: 600; color: #f2f5f7; }"
            "QLabel#tileMeta { color: #9aa7b2; font-size: 11px; }"
            "QLabel#badge { border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #d8e1e8; background: #2b3440; }"
            "QLabel#progressBadge { border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #332400; background: #d8b65a; }"
        )

    def _build_queue_tile(
        self,
        unit,
        est_seconds: float,
        retention: float | None,
        progression_reason: str | None = None,
        is_done: bool = False,
        actual_seconds: float | None = None,
    ) -> QWidget:
        root = QFrame()
        root.setObjectName("queueTile")
        root.setProperty("queue_done", bool(is_done))
        self._apply_queue_tile_style(root, bool(is_done), glow_strength=0.0)

        lay = QVBoxLayout(root)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        title = QLabel(unit.title)
        title.setObjectName("tileTitle")
        title.setWordWrap(True)

        hierarchy_tail = unit.hierarchy_path
        source_prefix = f"{unit.source_title}"
        meta = QLabel(f"{source_prefix} · {hierarchy_tail} · reviews: {unit.review_count}")
        meta.setObjectName("tileMeta")
        meta.setWordWrap(True)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)
        pages = QLabel(f"pages {unit.start_page}-{unit.end_page}"); pages.setObjectName("badge")
        if is_done and actual_seconds is not None:
            mins = QLabel(self._format_actual_time(actual_seconds))
        else:
            mins = QLabel(self._format_estimated_time(est_seconds))
        mins.setObjectName("badge")
        if retention is None:
            retention_lbl = QLabel("new")
            retention_lbl.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #d9e8ff; background: #22345a;")
            ret_pct = None
        else:
            ret_pct = max(1, min(99, int(round(retention * 100))))
            retention_lbl = QLabel(f"retention {ret_pct}%")
        retention_lbl.setObjectName("badge")
        if ret_pct is None:
            pass
        elif ret_pct < 35:
            retention_lbl.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #ffd7d7; background: #5a2222;")
        elif ret_pct < 60:
            retention_lbl.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #ffeecf; background: #5a4a22;")
        else:
            retention_lbl.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #d2f2dc; background: #1f4d32;")

        for w in [pages, mins, retention_lbl]:
            badge_row.addWidget(w)
        if is_done:
            done_badge = QLabel("Done")
            done_badge.setObjectName("progressBadge")
            done_badge.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #f4eddc; background: #5f5672;")
            badge_row.addWidget(done_badge)
        if progression_reason:
            label_map = {
                "manual": "Manually added",
                "low_retention": "Low retention",
            }
            progress_text = label_map.get(progression_reason, progression_reason)
            progress_badge = QLabel(progress_text)
            progress_badge.setObjectName("progressBadge")
            if progression_reason == "manual":
                progress_badge.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #eef2ff; background: #3b2f6b;")
            elif progression_reason == "low_retention":
                progress_badge.setStyleSheet("border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #fff2df; background: #6a4a1e;")
            badge_row.addWidget(progress_badge)
        badge_row.addStretch()

        retention_text = "new" if retention is None else f"{int(round(max(0.01, min(0.99, retention)) * 100))}%"
        hover = (
            f"Source: {unit.source_title}\n"
            f"Hierarchy: {unit.hierarchy_path}\n"
            f"Pages: {unit.start_page}-{unit.end_page}\n"
            f"Estimated review: {self._format_estimated_time(est_seconds)}\n"
            f"Retention: {retention_text}\n"
            f"Reviews done: {unit.review_count}\n"
            f"Next due: {unit.next_review_at or 'now'}"
        )
        root.setToolTip(hover)

        lay.addWidget(title)
        lay.addWidget(meta)
        lay.addLayout(badge_row)
        return root

    def _format_estimated_time(self, est_seconds: float) -> str:
        seconds = max(1, int(round(est_seconds)))
        if seconds < 90:
            return f"~{seconds}s"
        mins = seconds / 60.0
        return f"~{mins:.1f} min"

    def _format_actual_time(self, elapsed_seconds: float) -> str:
        seconds = max(0, int(round(float(elapsed_seconds))))
        if seconds < 90:
            return f"{seconds}s"
        mins = seconds / 60.0
        return f"{mins:.1f} min"

    def _refresh_today_strip(self, total_planned: int, planned_ids: list[int], queue_signature: str) -> None:
        window = self._today_window()
        settings_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        fallback_unit_seconds = self.settings_repo.get("fallback_review_seconds_per_unit", "90")
        fallback_page_seconds = self.settings_repo.get("fallback_review_seconds_per_page", "60")
        latest = self.review_repo.review_summary_between(window["start_utc_iso"], window["next_start_utc_iso"])
        cache_key = (
            f"{window['day_key']}|{window['offset']}|{settings_minutes}|"
            f"{fallback_unit_seconds}|{fallback_page_seconds}|"
            f"{queue_signature}|{latest['max_review_id']}"
        )
        cached = self._cache_get(cache_key)
        if cached is None:
            reviewed = int(latest["review_count"])
            total_seconds = float(latest["total_seconds"])
            page_summary = self.review_repo.reviewed_unit_page_summary_between(
                window["start_utc_iso"],
                window["next_start_utc_iso"],
            )
            reviewed_pages = int(page_summary["reviewed_pages_sum"])
            planned_units = self.review_repo.unit_views_by_ids(planned_ids)
            total_pages = sum(max(1, (int(u.end_page) - int(u.start_page)) + 1) for u in planned_units)
            planned_estimated_seconds = sum(float(self._estimate_review_seconds(u)) for u in planned_units)
            mins_per_page = 0.0 if reviewed_pages <= 0 else (total_seconds / 60.0) / float(reviewed_pages)
            cached = self._cache_set(
                cache_key,
                {
                    "reviewed": reviewed,
                    "total_planned": int(total_planned),
                    "reviewed_pages": reviewed_pages,
                    "total_pages": int(total_pages),
                    "mins_per_page": mins_per_page,
                    "total_seconds": total_seconds,
                    "planned_estimated_seconds": planned_estimated_seconds,
                },
            )
        no_reviews_hint = " · No reviews yet today" if int(cached["reviewed"]) == 0 else ""
        self.today_strip.setText(
            "Today so far  |  "
            f"Units reviewed: {cached['reviewed']}/{cached['total_planned']}  |  "
            f"Pages reviewed: {cached['reviewed_pages']}/{cached['total_pages']}  |  "
            f"Minutes per page: {cached['mins_per_page']:.1f}  |  "
            f"Minutes learned today: {format_minutes_whole(cached['total_seconds'])}/{format_minutes_whole(cached['planned_estimated_seconds'])}"
            f"{no_reviews_hint}"
        )

    def _refresh_queue_doc_progress(self, current_page: int | None = None) -> None:
        if not self.active_unit:
            self.queue_doc_progress.set_data([], total_pages=1, current_page=1)
            return
        source = self.source_repo.get(self.active_unit.source_id)
        if not source:
            self.queue_doc_progress.set_data([], total_pages=1, current_page=1)
            return

        rows = self.review_repo.db.conn.execute(
            """SELECT u.*, n.depth AS depth
            FROM units u
            LEFT JOIN outline_nodes n ON n.id=u.node_id
            WHERE u.source_id=?
            ORDER BY u.start_page, u.title""",
            (self.active_unit.source_id,),
        ).fetchall()
        outline_rows = self.review_repo.db.conn.execute(
            """SELECT id,parent_id,title,depth,start_page
            FROM outline_nodes
            WHERE source_id=?
            ORDER BY order_index""",
            (self.active_unit.source_id,),
        ).fetchall()
        row_by_id = {int(r["id"]): r for r in outline_rows}
        parent_by_id = {int(r["id"]): (int(r["parent_id"]) if r["parent_id"] is not None else None) for r in outline_rows}

        def hierarchy_path_for_node(node_id: int) -> str:
            parts: list[str] = []
            cursor: int | None = int(node_id)
            seen: set[int] = set()
            while cursor is not None and cursor not in seen:
                seen.add(cursor)
                row = row_by_id.get(cursor)
                if not row:
                    break
                parts.append(str(row["title"]))
                cursor = parent_by_id.get(cursor)
            parts.reverse()
            return " > ".join(parts)

        division_markers: list[dict] = []
        seen_division_pages: set[int] = set()
        for row in outline_rows:
            depth = int(row["depth"] or 0)
            page = int(row["start_page"] or 0)
            if depth < 1 or depth > 2 or page < 1 or page in seen_division_pages:
                continue
            seen_division_pages.add(page)
            division_markers.append({
                "title": row["title"],
                "hierarchy_path": hierarchy_path_for_node(int(row["id"])),
                "page": page,
                "depth": depth,
            })

        now = now_utc()
        segs: list[dict] = []
        for u in rows:
            state = "unstarted"
            ret = None
            if u["review_count"] > 0:
                state = "learning"
                ret = retention_estimate(u, now)
                nr = u["next_review_at"]
                if nr:
                    try:
                        next_dt = parse_iso_to_utc(nr)
                        if ret >= 0.9 and (next_dt - now) >= timedelta(days=180):
                            state = "mastered"
                    except Exception:
                        pass
            segs.append({
                "title": u["title"],
                "start_page": int(u["start_page"]),
                "end_page": int(u["end_page"]),
                "state": state,
                "retention": ret,
                "depth": int(u["depth"] or 3),
                "hierarchy_path": hierarchy_path_for_node(int(u["node_id"])),
            })

        cp = current_page if current_page is not None else int(self.pdf.view_state().get("page", self.active_unit.start_page))
        self.queue_doc_progress.set_data(
            segs,
            total_pages=int(source.page_count or 1),
            current_page=int(cp),
            division_markers=sorted(division_markers, key=lambda m: (int(m["page"]), int(m["depth"]))),
        )

    def _display_index_for_row(self, row: int) -> int:
        if row < 0:
            return -1
        item = self.list.item(row)
        if not item:
            return -1
        mapped = item.data(256)
        return int(mapped) if isinstance(mapped, int) else -1

    def _add_done_delimiter_row(self) -> None:
        item = QListWidgetItem()
        item.setFlags(Qt.NoItemFlags)
        item.setData(256, -1)
        line = QFrame()
        line_l = QHBoxLayout(line)
        line_l.setContentsMargins(8, 4, 8, 4)
        line_l.setSpacing(8)
        left = QFrame(); left.setFrameShape(QFrame.HLine); left.setStyleSheet("color:#5d6880;")
        right = QFrame(); right.setFrameShape(QFrame.HLine); right.setStyleSheet("color:#5d6880;")
        label = QLabel("Done")
        label.setStyleSheet("color:#d6c89b; font-size:11px; font-weight:600;")
        line_l.addWidget(left, 1)
        line_l.addWidget(label, 0)
        line_l.addWidget(right, 6)
        item.setSizeHint(QSize(220, 22))
        self.list.addItem(item)
        self.list.setItemWidget(item, line)

    def pick_unit(self, idx):
        self._save_current_draft()
        mapped_idx = self._display_index_for_row(idx)
        if idx >= 0 and mapped_idx < 0:
            return
        if mapped_idx < 0 or mapped_idx >= len(self.display_units):
            self.active_unit = None
            self.hint_markdown_text = ""
            self.hint_last_changed_at = None
            self._refresh_note_previews()
            self.queue_outline_tree.clear()
            self._queue_outline_items = {}
            self._queue_outline_rows_by_id = {}
            self._queue_outline_child_ids = {}
            self._active_queue_outline_node_id = None
            self.queue_text_layer_hint.setText("Text layer: unknown")
            self.review_history_list.clear()
            return
        self.active_unit = self.display_units[mapped_idx][0]
        self.title.setText(f"{self.active_unit.source_title} — {self.active_unit.title}")
        path = self.source_path_cache.get(self.active_unit.source_id)
        if not path:
            s = self.source_repo.get(self.active_unit.source_id)
            path = s.file_path if s else ""
            if path:
                self.source_path_cache[self.active_unit.source_id] = path
        last_zoom = self.pdf.zoom_factor()
        self.pdf.load_if_needed(path)
        self.pdf.set_multi_page_mode()
        self._refresh_queue_text_layer_hint(path or "")
        zoom_setting = self.settings_repo.get_ui_state("queue_pdf_zoom", "")
        try:
            target_zoom = float(zoom_setting) if zoom_setting else float(last_zoom)
        except Exception:
            target_zoom = float(last_zoom)
        self.pdf.set_zoom(max(0.25, min(4.0, target_zoom)))
        self._refresh_queue_outline_tree(self.active_unit.source_id)
        self.pdf.set_page(self.active_unit.start_page)
        self._set_active_queue_outline_by_page(int(self.active_unit.start_page))
        self._load_draft_for_active()
        self.hint_markdown_text = self.review_repo.unit_hint_markdown(self.active_unit.unit_id)
        self.hint_last_changed_at = self.review_repo.unit_hint_last_changed_at(self.active_unit.unit_id)
        self._refresh_note_previews()
        self._refresh_queue_history_panel()
        self._queue_last_pdf_page = int(self.pdf.view_state().get("page", self.active_unit.start_page))
        self._refresh_queue_doc_progress(self._queue_last_pdf_page)
        self._sync_queue_pdf_overlays()

    def jump_to_active_unit(self):
        if self.active_unit:
            self.pdf.set_page(self.active_unit.start_page)
            self._sync_queue_pdf_overlays()

    def tick(self):
        if self.timer_running:
            self.timer_seconds += 1
            self.timer_tile.time_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")
        if self.active_unit:
            page = int(self.pdf.view_state().get("page", self._queue_last_pdf_page or 1))
            if page != self._queue_last_pdf_page:
                self._queue_last_pdf_page = page
                self._set_active_queue_outline_by_page(page)
                self._refresh_queue_doc_progress(page)
                self._sync_queue_pdf_overlays()

    def start_timer(self):
        self.timer_running = True
        if not self.started_at:
            self.started_at = now_utc()
        self.timer_tile.set_running(True)

    def pause_timer(self):
        self.timer_running = False
        self.timer_tile.set_running(False)

    def reset_timer(self):
        self.timer_seconds = 0
        self.started_at = None
        self.timer_running = False
        self.timer_tile.time_lbl.setText("00:00")
        self.timer_tile.set_running(False)
        if self.active_unit and self.active_unit.unit_id in self.unit_drafts:
            self.unit_drafts[self.active_unit.unit_id]["timer_seconds"] = 0
            self.unit_drafts[self.active_unit.unit_id]["timer_running"] = False
            self.unit_drafts[self.active_unit.unit_id]["started_at"] = ""

    def rate(self, rating: str):
        if not self.active_unit:
            return
        self._selected_rating = rating
        self._update_rating_buttons_ui()
        now = now_utc()
        unit_row = self.review_repo.unit_by_id(self.active_unit.unit_id)
        history = self.review_repo.events_for_unit_chronological(self.active_unit.unit_id)
        # Keep scheduling aligned with the explicit app timezone setting used for "today" boundaries.
        tzinfo = parse_gmt_offset(normalized_gmt_offset(self.settings_repo.get("timezone_gmt_offset", "+00:00")))
        try:
            desired_retention = float(self.settings_repo.get("desired_retention", "0.90"))
        except Exception:
            desired_retention = 0.9
        fsrs_result = schedule_next_review(
            unit_row=unit_row,
            review_events=history,
            now=now,
            feedback=rating,
            timezone_info=tzinfo,
            desired_retention=desired_retention,
            params=DEFAULT_FSRS_PARAMETERS,
        )
        payload = {
            "started_at": iso_utc(self.started_at or now),
            "ended_at": iso_utc(now),
            "elapsed_seconds": self.timer_seconds,
            "rating": rating,
            "pre_note": self.pre_note_text,
            "post_note": self.post_note_text,
        }
        count = unit_row["review_count"] + 1
        avg = ((unit_row["avg_rating"] * unit_row["review_count"]) + {"easy": 5, "with_effort": 3, "hard": 2, "skip": 1}[rating]) / count
        unit_stats = {
            "last_review_at": iso_utc(now),
            "review_count": count,
            "ease_factor": unit_row["ease_factor"],
            "avg_rating": avg,
            "fsrs_difficulty": fsrs_result.state.difficulty,
            "fsrs_stability": fsrs_result.state.stability,
            "fsrs_last_review_at": iso_utc(fsrs_result.state.last_review_at),
            "fsrs_last_grade": int(fsrs_result.state.last_grade),
            "fsrs_review_count": int(fsrs_result.state.review_count),
            "fsrs_lapse_count": int(fsrs_result.state.lapse_count),
            "fsrs_state_version": int(fsrs_result.state.state_version),
            "fsrs_due_retention_used": fsrs_result.due_retention_used,
        }
        self.review_repo.record_review(self.active_unit.unit_id, payload, unit_stats)
        self._invalidate_analytics_cache()
        self.unit_drafts.pop(self.active_unit.unit_id, None)
        self.reset_timer()
        self.pre_note_text = ""
        self.post_note_text = ""
        self._refresh_note_previews()
        self.refresh()

    def open_history(self):
        if not self.active_unit:
            return
        events = self.review_repo.events_for_unit(self.active_unit.unit_id)
        dlg = ReviewHistoryDialog(events, self.review_repo, self)
        dlg.exec()
        if getattr(dlg, "changed", False):
            self._invalidate_analytics_cache()
            self.refresh()

    def _refresh_note_previews(self) -> None:
        pre = (self.pre_note_text or "").strip()
        post = (self.post_note_text or "").strip()
        hint = (self.hint_markdown_text or "").strip()
        pre_label = "Edit Pre-recall Note ✓" if pre else "Edit Pre-recall Note"
        post_label = "Edit Post-recall Note ✓" if post else "Edit Post-recall Note"
        hint_label = "Hint ✓" if hint else "Hint"
        self.pre_note_btn.setText(pre_label)
        self.hint_btn.setText(hint_label)
        self.post_note_btn.setText(post_label)
        self.pre_note_btn.setToolTip(pre[:220] if pre else "No pre-recall note")
        hint_tip = hint[:220] if hint else "No hint"
        if self.hint_last_changed_at:
            hint_tip = f"{hint_tip}\nLast edited: {self.hint_last_changed_at}"
        self.hint_btn.setToolTip(hint_tip)
        self.post_note_btn.setToolTip(post[:220] if post else "No post-recall note")

    def edit_pre_note(self) -> None:
        dlg = RecallNoteDialog("Pre-recall Note", self.pre_note_text, self)
        if dlg.exec():
            self.pre_note_text = dlg.value()
            self._refresh_note_previews()

    def edit_post_note(self) -> None:
        dlg = RecallNoteDialog("Post-recall Note", self.post_note_text, self)
        if dlg.exec():
            self.post_note_text = dlg.value()
            self._refresh_note_previews()

    def edit_hint(self) -> None:
        if not self.active_unit:
            return
        if self._hint_dialog and self._hint_dialog.isVisible():
            self._hint_dialog.raise_()
            self._hint_dialog.activateWindow()
            return
        dlg = HintMarkdownDialog(self.hint_markdown_text, self)
        dlg.set_keep_on_top(self._hint_keep_on_top)
        dlg.keep_on_top_changed.connect(self._on_hint_keep_on_top_changed)
        dlg.accepted.connect(self._save_hint_from_dialog)
        dlg.finished.connect(self._on_hint_dialog_finished)
        self._hint_dialog = dlg
        self._set_hint_editing_controls_locked(True)
        dlg.show()

    def _on_hint_keep_on_top_changed(self, enabled: bool) -> None:
        self._hint_keep_on_top = bool(enabled)

    def _save_hint_from_dialog(self) -> None:
        if not self.active_unit or not self._hint_dialog:
            return
        self.hint_markdown_text = self._hint_dialog.value()
        self.review_repo.save_unit_hint_markdown(self.active_unit.unit_id, self.hint_markdown_text)
        self.hint_last_changed_at = self.review_repo.unit_hint_last_changed_at(self.active_unit.unit_id)
        self._refresh_note_previews()

    def _on_hint_dialog_finished(self, _result: int) -> None:
        self._set_hint_editing_controls_locked(False)
        self._hint_dialog = None

    def _set_hint_editing_controls_locked(self, locked: bool) -> None:
        if locked:
            self._hint_temporarily_disabled_buttons = []
            for btn in self.findChildren(QPushButton):
                if not btn.isEnabled():
                    continue
                btn.setEnabled(False)
                self._hint_temporarily_disabled_buttons.append(btn)
            return
        for btn in self._hint_temporarily_disabled_buttons:
            try:
                btn.setEnabled(True)
            except RuntimeError:
                continue
        self._hint_temporarily_disabled_buttons = []

    def _refresh_queue_history_panel(self) -> None:
        self.review_history_list.clear()
        if not self.active_unit:
            return
        events = self.review_repo.events_for_unit(self.active_unit.unit_id)[:8]
        for ev in events:
            ended = str(ev["ended_at"] or "")
            day = ended[:10] if len(ended) >= 10 else ended
            text = f"• {day} · {ev['rating']} · {ev['elapsed_seconds']}s"
            self.review_history_list.addItem(text)


class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, settings_repo: SettingsRepo, review_repo: ReviewRepo):
        super().__init__()
        self.settings_repo = settings_repo
        self.review_repo = review_repo
        form = QFormLayout()
        self.daily = QSpinBox(); self.daily.setRange(15, 600); self.daily.setValue(int(settings_repo.get("daily_minutes", "90")))
        self.min_ret = QSpinBox(); self.min_ret.setRange(10, 90); self.min_ret.setValue(int(settings_repo.get("min_retention_percent", "45")))
        self.new_cap = QSpinBox(); self.new_cap.setRange(0, 50); self.new_cap.setValue(int(settings_repo.get("new_units_cap", "6")))
        self.fallback_unit_seconds = QSpinBox(); self.fallback_unit_seconds.setRange(10, 3600); self.fallback_unit_seconds.setValue(int(settings_repo.get("fallback_review_seconds_per_unit", "90")))
        self.fallback_page_seconds = QSpinBox(); self.fallback_page_seconds.setRange(5, 1800); self.fallback_page_seconds.setValue(int(settings_repo.get("fallback_review_seconds_per_page", "60")))
        self.timezone_offset = QLineEdit()
        self.timezone_offset.setPlaceholderText("+00:00")
        self.timezone_offset.setText(normalized_gmt_offset(settings_repo.get("timezone_gmt_offset", "+00:00")))
        self.timezone_help = QLabel("Used for day boundaries in Today stats.")
        self.timezone_help.setStyleSheet("color:#9aa7b2;")
        self.timezone_error = QLabel("")
        self.timezone_error.setStyleSheet("color:#f4b4b4;")
        form.addRow("Daily target minutes", self.daily)
        form.addRow("Low retention threshold %", self.min_ret)
        form.addRow("Max new units/day", self.new_cap)
        form.addRow("Fallback sec/unit", self.fallback_unit_seconds)
        form.addRow("Fallback sec/page", self.fallback_page_seconds)
        form.addRow("Timezone (GMT offset ±HH:MM)", self.timezone_offset)
        form.addRow("", self.timezone_help)
        form.addRow("", self.timezone_error)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        self.summary = QLabel("")
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(save); lay.addWidget(self.summary); lay.addStretch()
        self.refresh_summary()

    def save(self):
        offset = (self.timezone_offset.text() or "").strip()
        if not is_valid_gmt_offset(offset):
            self.timezone_error.setText("Timezone must match ±HH:MM (examples: +00:00, -05:00, +05:30).")
            return
        self.timezone_error.setText("")
        self.settings_repo.set("daily_minutes", str(self.daily.value()))
        self.settings_repo.set("min_retention_percent", str(self.min_ret.value()))
        self.settings_repo.set("new_units_cap", str(self.new_cap.value()))
        self.settings_repo.set("fallback_review_seconds_per_unit", str(self.fallback_unit_seconds.value()))
        self.settings_repo.set("fallback_review_seconds_per_page", str(self.fallback_page_seconds.value()))
        self.settings_repo.set("timezone_gmt_offset", offset)
        self.refresh_summary()
        self.settings_changed.emit()

    def refresh_summary(self):
        self._runtime_estimation_model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
        due_units = self.review_repo.due_units(iso_utc(now_utc()))
        due_review_minutes = sum(self._estimate_review_seconds(u) for u in due_units) / 60.0
        new_units = self.review_repo.new_units()
        if new_units:
            avg_new_unit_seconds = sum(self._estimate_review_seconds(u) for u in new_units) / max(1, len(new_units))
        else:
            avg_new_unit_seconds = float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
        allocation = allocate_new_units(
            due_review_minutes=due_review_minutes,
            daily_minutes=self.daily.value(),
            avg_new_unit_seconds=avg_new_unit_seconds,
            new_units_cap=self.new_cap.value(),
        )
        guardrail = recommend_new_units_with_guardrail(
            due_review_minutes=due_review_minutes,
            daily_minutes=self.daily.value(),
            avg_new_unit_seconds=avg_new_unit_seconds,
            new_units_cap=self.new_cap.value(),
            horizon_days=10,
            safety_threshold=0.9,
        )
        recommendation = "review-only day" if guardrail.recommended_new_units == 0 else f"up to {guardrail.recommended_new_units} new units"
        explanation = ""
        if guardrail.limiting_day is not None and guardrail.recommended_new_units < allocation.suggested_new_units:
            explanation = (
                f"\nGuardrail: capped to avoid day {guardrail.limiting_day} exceeding ~90% budget "
                f"({guardrail.limiting_projected_minutes:.1f} min projected)."
            )
        self.summary.setText(
            f"Due now: {len(due_units)}\n"
            f"Projected review load: {allocation.review_minutes:.1f} min\n"
            f"Free budget after reviews: {allocation.free_minutes:.1f} min\n"
            f"Scheduler recommendation: {recommendation}"
            f"{explanation}"
        )

    def _estimate_review_seconds(self, unit) -> float:
        model = getattr(self, "_runtime_estimation_model", None)
        if model is None:
            model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
            self._runtime_estimation_model = model
        return model.estimate_unit_seconds(unit, now_utc())


class StatisticsPage(QWidget):
    def __init__(self, review_repo: ReviewRepo, settings_repo: SettingsRepo, source_repo: SourceRepo):
        super().__init__()
        self.review_repo = review_repo
        self.settings_repo = settings_repo
        self.source_repo = source_repo

        root = QVBoxLayout(self)
        root.setSpacing(8)

        headline = QLabel("Statistics")
        root.addWidget(headline)

        top_panel = QFrame()
        top_panel.setObjectName("panel")
        top_grid = QGridLayout(top_panel)
        top_grid.setContentsMargins(8, 8, 8, 8)
        top_grid.setHorizontalSpacing(14)
        top_grid.setVerticalSpacing(6)

        self.metric_labels: dict[str, QLabel] = {}
        metric_rows = [
            ("Time studied (7d)", "time_studied"),
            ("Cards/units studied (7d)", "units_studied"),
            ("Daily goal", "daily_goal"),
            ("Current streak", "current_streak"),
            ("Due now", "due_now"),
            ("Avg retention (learned)", "avg_retention"),
            ("Low retention (learned)", "low_retention"),
            ("Learned units", "learned_units"),
        ]
        for idx, (label, key) in enumerate(metric_rows):
            row = idx // 4
            col = (idx % 4) * 2
            top_grid.addWidget(QLabel(label), row, col)
            value = QLabel("—")
            top_grid.addWidget(value, row, col + 1)
            self.metric_labels[key] = value
        root.addWidget(top_panel)

        mid = QHBoxLayout()

        trend_panel = QFrame()
        trend_panel.setObjectName("panel")
        trend_l = QVBoxLayout(trend_panel)
        trend_l.setContentsMargins(8, 8, 8, 8)
        trend_l.addWidget(QLabel("Last 7 days"))
        self.weekly_trend = QTreeWidget()
        self.weekly_trend.setHeaderLabels(["Day", "Reviews", "Minutes"])
        _enable_smooth_scroll(self.weekly_trend)
        self.weekly_trend.setMaximumHeight(210)
        trend_l.addWidget(self.weekly_trend)
        mid.addWidget(trend_panel, 2)

        perf_panel = QFrame()
        perf_panel.setObjectName("panel")
        perf_l = QVBoxLayout(perf_panel)
        perf_l.setContentsMargins(8, 8, 8, 8)
        perf_l.addWidget(QLabel("Performance (7d)"))
        self.rating_bars: dict[str, QProgressBar] = {}
        self.rating_labels: dict[str, QLabel] = {}
        for key, label in [
            ("skip", "Skip"),
            ("hard", "Hard"),
            ("with_effort", "Partially Recalled"),
            ("easy", "Easily Recalled"),
        ]:
            row = QHBoxLayout()
            name = QLabel(label)
            name.setMinimumWidth(110)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            pct = QLabel("0%")
            pct.setMinimumWidth(38)
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(pct)
            perf_l.addLayout(row)
            self.rating_bars[key] = bar
            self.rating_labels[key] = pct
        perf_l.addStretch()
        mid.addWidget(perf_panel, 1)
        root.addLayout(mid)

        self.sources_table = QTreeWidget()
        self.sources_table.setHeaderLabels(["Source", "Units", "Learned", "Due", "Avg rating", "Avg time", "Last review"])
        _enable_smooth_scroll(self.sources_table)
        root.addWidget(QLabel("By Source"))
        root.addWidget(self.sources_table, 1)

        self.refresh()

    def _rating_breakdown(self, rows: list[dict]) -> dict[str, int]:
        counts = {"easy": 0, "with_effort": 0, "hard": 0, "skip": 0}
        for r in rows:
            rating = str(r["rating"] or "")
            if rating in counts:
                counts[rating] += 1
        return counts

    def _streak_days(self, day_hits: set[str]) -> int:
        streak = 0
        cursor = now_utc().date()
        while True:
            key = cursor.isoformat()
            if key in day_hits:
                streak += 1
                cursor = cursor - timedelta(days=1)
                continue
            break
        return streak

    def refresh(self) -> None:
        now = now_utc()
        conn = self.review_repo.db.conn
        since_week = iso_utc(now - timedelta(days=6))
        since_month = iso_utc(now - timedelta(days=31))

        recent_rows = conn.execute(
            "SELECT ended_at,elapsed_seconds,rating FROM review_events WHERE deleted_at IS NULL AND ended_at>=? ORDER BY ended_at ASC",
            (since_month,),
        ).fetchall()

        seven_days: list[str] = [(now - timedelta(days=i)).date().isoformat() for i in range(6, -1, -1)]
        day_counts = {d: 0 for d in seven_days}
        day_seconds = {d: 0 for d in seven_days}
        week_seconds = 0
        week_rows: list[dict] = []
        active_days: set[str] = set()
        for r in recent_rows:
            try:
                ended_dt = parse_iso_to_utc(str(r["ended_at"]))
            except Exception:
                continue
            day_key = ended_dt.date().isoformat()
            if day_key in day_counts:
                day_counts[day_key] += 1
                day_seconds[day_key] += int(r["elapsed_seconds"] or 0)
            active_days.add(day_key)
            if ended_dt >= parse_iso_to_utc(since_week):
                week_seconds += int(r["elapsed_seconds"] or 0)
                week_rows.append(r)

        hrs = week_seconds // 3600
        mins = (week_seconds % 3600) // 60
        studied_units = sum(self._rating_breakdown(week_rows).values())
        self.metric_labels["time_studied"].setText(f"{hrs}h {mins}m")
        self.metric_labels["units_studied"].setText(str(studied_units))

        rating_counts = self._rating_breakdown(week_rows)
        total = max(1, sum(rating_counts.values()))
        for key, cnt in rating_counts.items():
            pct = int(round((cnt / total) * 100))
            self.rating_bars[key].setValue(pct)
            self.rating_labels[key].setText(f"{pct}%")

        due_now = len(self.review_repo.due_units(iso_utc(now)))
        daily_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        model = _build_runtime_model_for_request(self.review_repo, self.settings_repo)
        sample_units = self.review_repo.due_units(iso_utc(now))
        if not sample_units:
            sample_units = self.review_repo.new_units()
        if sample_units:
            avg_secs = sum(model.estimate_unit_seconds(u, now) for u in sample_units) / max(1, len(sample_units))
        else:
            avg_secs = float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
        est_units = max(1, int((daily_minutes * 60) / max(1.0, float(avg_secs))))
        self.metric_labels["daily_goal"].setText(f"{est_units} units")
        self.metric_labels["current_streak"].setText(f"{self._streak_days(active_days)} day(s)")
        self.metric_labels["due_now"].setText(f"{due_now} units")

        unit_rows = conn.execute("SELECT * FROM units").fetchall()
        learned_rows = [u for u in unit_rows if int(u["review_count"] or 0) > 0 and u["last_review_at"]]
        if learned_rows:
            avg_ret = sum(retention_estimate(u, now) for u in learned_rows) / len(learned_rows)
            low_ret = sum(1 for u in learned_rows if retention_estimate(u, now) < 0.45)
            self.metric_labels["avg_retention"].setText(f"{avg_ret:.0%}")
            self.metric_labels["low_retention"].setText(str(low_ret))
        else:
            self.metric_labels["avg_retention"].setText("n/a")
            self.metric_labels["low_retention"].setText("0")
        self.metric_labels["learned_units"].setText(str(len(learned_rows)))

        self.weekly_trend.clear()
        for d in seven_days:
            day_dt = parse_iso_to_utc(f"{d}T00:00:00+00:00")
            minutes = int(round(day_seconds[d] / 60.0))
            self.weekly_trend.addTopLevelItem(QTreeWidgetItem([
                day_dt.strftime("%a"),
                str(day_counts[d]),
                str(minutes),
            ]))

        self.sources_table.clear()
        for row in self.review_repo.source_statistics(iso_utc(now)):
            item = QTreeWidgetItem([
                str(row["source_title"] or ""),
                str(int(row["total_units"] or 0)),
                str(int(row["learned_units"] or 0)),
                str(int(row["due_units"] or 0)),
                f"{float(row['avg_rating_learned'] or 0):.2f}",
                f"{(float(row['avg_elapsed_seconds'] or 0) / 60.0):.1f}m",
                str(row["last_review_at"] or "—"),
            ])
            self.sources_table.addTopLevelItem(item)

class MainWindow(QMainWindow):
    def __init__(self, source_repo: SourceRepo, outline_repo: OutlineRepo, review_repo: ReviewRepo, settings_repo: SettingsRepo, highlight_repo: HighlightRepo, pdf_service: PdfService):
        super().__init__()
        self.setWindowTitle("Oh Well One More Time — Study PDF")
        self.highlight_repo = highlight_repo
        self.resize(1500, 900)

        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        bar = QFrame(); bar.setObjectName("panel")
        b = QHBoxLayout(bar)
        b.addWidget(QLabel("📘 Study Dashboard"))
        b.addStretch()
        lay.addWidget(bar)

        tabs = QTabWidget()
        self.tabs = tabs
        self.queue = StudyQueuePage(source_repo, review_repo, settings_repo, highlight_repo, pdf_service)
        self.sources = SourcesPage(source_repo, outline_repo, review_repo, highlight_repo, settings_repo, pdf_service)
        self.settings = SettingsPage(settings_repo, review_repo)
        self.stats = StatisticsPage(review_repo, settings_repo, source_repo)
        tabs.addTab(self.queue, "Study Queue")
        tabs.addTab(self.sources, "Sources")
        tabs.addTab(self.stats, "Statistics")
        tabs.addTab(self.settings, "Settings")
        lay.addWidget(tabs)

        self._sync_delay_ms = 5000
        self._queue_dirty = True
        self._queue_sync_timer = QTimer(self)
        self._queue_sync_timer.setSingleShot(True)
        self._queue_sync_timer.timeout.connect(self._flush_debounced_updates)

        self.sources.library_changed.connect(self.sync_queue_views)
        self.sources.queue_changed.connect(self.request_sync_queue_views)
        self.sources.add_to_today_queue_requested.connect(self._on_add_to_today_queue_requested)
        self.settings.settings_changed.connect(self.sync_queue_views)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def sync_queue_views(self):
        if self._queue_sync_timer.isActive():
            self._queue_sync_timer.stop()
        self._queue_dirty = True
        self._flush_now(force_queue=True)

    def request_sync_queue_views(self):
        self._queue_dirty = True
        if not self._queue_sync_timer.isActive():
            self.queue.invalidate_session_queue_snapshot()
            if self.tabs.currentIndex() == 0:
                self.queue.show_recalculation_pending()
        self._queue_sync_timer.start(self._sync_delay_ms)

    def _flush_now(self, force_queue: bool = False):
        if force_queue or self.tabs.currentIndex() == 0:
            self.queue.refresh()
            self._queue_dirty = False
        self.settings.refresh_summary()
        self.stats.refresh()

    def _flush_debounced_updates(self):
        self._flush_now(force_queue=False)

    def _on_tab_changed(self, index: int) -> None:
        if index == 0:
            if self._queue_sync_timer.isActive():
                self.queue.show_recalculation_pending()
                return
            if self._queue_dirty:
                self._flush_now(force_queue=True)
            return
        if index == 2:
            self.stats.refresh()

    def _on_add_to_today_queue_requested(self, source_id: int, page: int) -> None:
        self.queue.add_unit_to_today_queue(int(source_id), int(page))
        self.request_sync_queue_views()

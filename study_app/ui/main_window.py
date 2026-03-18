from __future__ import annotations

import json
from pathlib import Path
from datetime import timedelta

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QCursor, QPainter, QPen, QLinearGradient
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
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

from study_app.persistence.repositories import HighlightRepo, OutlineRepo, ReviewRepo, SettingsRepo, SourceRepo
from study_app.pdf.pdf_service import PdfService
from study_app.services.outline_service import entries_to_text
from study_app.services.queue_planner import plan_session_queue
from study_app.services.scheduler import allocate_new_units, compute_next, recommend_new_units_with_guardrail, retention_estimate
from study_app.domain.models import iso_utc, now_utc, parse_iso_to_utc
from study_app.ui.dialogs import OutlineEditorDialog, ReviewHistoryDialog, SourceMetadataDialog
from study_app.ui.pdf_viewer import PersistentPdfViewer


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
            )
            self.current_workspace.queue_changed.connect(self.queue_changed.emit)
            self.current_workspace.context_changed.connect(self.on_workspace_context_changed)
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
    def __init__(
        self,
        source_id: int,
        source_repo: SourceRepo,
        outline_repo: OutlineRepo,
        review_repo: ReviewRepo,
        highlight_repo: HighlightRepo,
        settings_repo: SettingsRepo,
    ):
        super().__init__()
        self.source_id = source_id
        self.source_repo = source_repo
        self.outline_repo = outline_repo
        self.review_repo = review_repo
        self.highlight_repo = highlight_repo
        self.settings_repo = settings_repo
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
        l3.addWidget(self.source_hl_tree)
        tabs.addTab(tab_ins, "Insights")
        tabs.addTab(tab_unit, "Unit Highlights")
        tabs.addTab(tab_src, "Source Highlights")
        right_l = QVBoxLayout(); right_l.addWidget(tabs); right_l.addStretch()

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
        self._active_filter = ""
        self._last_pdf_page = 1
        self._pdf_page_poll = QTimer(self)
        self._pdf_page_poll.timeout.connect(self._on_pdf_page_polled)
        self._pdf_page_poll.start(450)
        self._apply_annotation_ui_state()
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
        self.refresh_tree()
        self.refresh_insights()
        self.refresh_highlights()
        self._last_pdf_page = int(self.pdf.view_state().get("page", 1))
        self._refresh_doc_progress(self._last_pdf_page)
        self._sync_pdf_overlay_highlights()
        if self.source:
            self.context_changed.emit(self.source.title, "")

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
        item.setText(1, "on" if cstate == Qt.Checked else "off" if cstate == Qt.Unchecked else "mixed")
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
        untouched = sum(1 for u in units if not u["last_review_at"])
        low = sum(1 for u in units if retention_estimate(u, now) < 0.45)
        avg = sum(retention_estimate(u, now) for u in units) / max(1, len(units))
        self.insights.setText(f"Total units: {len(units)}\nUntouched: {untouched}\nLow retention: {low}\nAvg retention: {avg:.0%}")

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


class StudyQueuePage(QWidget):
    def __init__(self, source_repo: SourceRepo, review_repo: ReviewRepo, settings_repo: SettingsRepo):
        super().__init__()
        self.source_repo = source_repo
        self.review_repo = review_repo
        self.settings_repo = settings_repo
        self.timer_seconds = 0
        self.timer_running = False
        self.active_unit = None
        self.started_at = None
        self.unit_drafts: dict[int, dict] = {}
        self.source_path_cache: dict[int, str] = {}
        self.display_units = []
        self._queue_last_pdf_page = 1

        self.list = QListWidget()
        _enable_smooth_scroll(self.list)
        self.list.setSpacing(6)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.viewport().installEventFilter(self)
        self.list.currentRowChanged.connect(self.pick_unit)
        self.list.itemDoubleClicked.connect(lambda *_: self.jump_to_active_unit())

        self.queue_banner = QLabel("")
        self.queue_banner.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(QLabel("Queue"))
        left.addWidget(self.queue_banner)
        left.addWidget(self.list)

        self.title = QLabel("No unit selected")
        self.timer_lbl = QLabel("00:00")
        self.pre = QTextEdit(); self.post = QTextEdit()
        self.pre.setMinimumHeight(140)
        self.post.setMinimumHeight(140)
        self.pre.setMaximumHeight(220)
        self.post.setMaximumHeight(220)
        timer_start = QPushButton("Start/Pause")
        timer_reset = QPushButton("Reset")
        timer_start.clicked.connect(self.toggle_timer)
        timer_reset.clicked.connect(self.reset_timer)
        self.pdf = PersistentPdfViewer()
        self.pdf.set_multi_page_mode()
        self.pdf.set_fit_mode()
        self.pdf.setMinimumHeight(760)
        self.queue_outline_tree = QTreeWidget()
        self.queue_outline_tree.setHeaderLabels(["Reading Context"])
        self.queue_outline_tree.setMaximumWidth(280)
        self.queue_outline_tree.setMinimumWidth(220)
        _enable_smooth_scroll(self.queue_outline_tree)
        self.queue_outline_tree.itemClicked.connect(self._on_queue_outline_click)
        self._queue_outline_items: dict[int, QTreeWidgetItem] = {}
        self._queue_outline_rows_by_id: dict[int, dict] = {}
        self._queue_outline_child_ids: dict[int, list[int]] = {}
        self._active_queue_outline_node_id: int | None = None
        hist_btn = QPushButton("Review History")
        hist_btn.clicked.connect(self.open_history)
        jump_btn = QPushButton("Jump to Unit")
        jump_btn.clicked.connect(self.jump_to_active_unit)

        ratings = QHBoxLayout()
        for label, r in [("Easy", "easy"), ("With Effort", "with_effort"), ("Hard", "hard"), ("Skip", "skip")]:
            b = QPushButton(label)
            b.clicked.connect(lambda _, rr=r: self.rate(rr))
            ratings.addWidget(b)

        content = QWidget()
        right = QVBoxLayout(content)
        right.addWidget(self.title)
        right.addWidget(self.timer_lbl); right.addWidget(timer_start); right.addWidget(timer_reset)
        right.addWidget(QLabel("Pre-recall note")); right.addWidget(self.pre)
        right.addWidget(QLabel("Post-recall note")); right.addWidget(self.post)
        right.addLayout(ratings); right.addWidget(jump_btn); right.addWidget(hist_btn)

        saved_h = self.settings_repo.get_ui_state("queue_pdf_height", "760")
        try:
            h = max(320, min(1600, int(saved_h)))
        except Exception:
            h = 760
        self.pdf.setMinimumHeight(h)
        self.pdf.setMaximumHeight(h)

        self.queue_doc_progress = DocumentProgressBar()
        self.queue_doc_progress.page_requested.connect(self._on_queue_doc_progress_page_requested)
        queue_pdf_row = QHBoxLayout()
        queue_pdf_row.addWidget(self.queue_outline_tree)
        queue_pdf_row.addWidget(self.pdf, 1)
        right.addLayout(queue_pdf_row, 1)
        right.addWidget(self.queue_doc_progress)

        corner_row = QHBoxLayout()
        corner_row.addStretch()
        self.resize_corner = CornerResizeHandle(self._resize_pdf_by_delta)
        corner_row.addWidget(self.resize_corner)
        right.addLayout(corner_row)
        right.addSpacing(90)
        right.addStretch()

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
        self.refresh()

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

    def _on_queue_outline_click(self, item, _col) -> None:
        page = int(item.data(0, 257) or 0)
        if page < 1:
            return
        self.pdf.set_page(page)
        self._queue_last_pdf_page = page
        self._set_active_queue_outline_by_page(page)
        self._refresh_queue_doc_progress(page)

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
            "pre": self.pre.toPlainText(),
            "post": self.post.toPlainText(),
            "timer_seconds": self.timer_seconds,
            "timer_running": self.timer_running,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "pdf_page": state["page"],
            "pdf_location": state["location"],
            "pdf_zoom": self.pdf.zoom_factor(),
        }
        self.settings_repo.set_ui_state("queue_pdf_zoom", str(self.pdf.zoom_factor()))

    def _load_draft_for_active(self):
        self.pre.clear(); self.post.clear()
        self.timer_seconds = 0
        self.timer_running = False
        self.started_at = None
        self.timer_lbl.setText("00:00")
        if not self.active_unit:
            return
        draft = self.unit_drafts.get(self.active_unit.unit_id)
        if not draft:
            return
        self.pre.setText(draft.get("pre", ""))
        self.post.setText(draft.get("post", ""))
        self.timer_seconds = int(draft.get("timer_seconds", 0))
        self.timer_running = bool(draft.get("timer_running", False))
        started = draft.get("started_at", "")
        if started:
            try:
                self.started_at = parse_iso_to_utc(started)
            except Exception:
                self.started_at = None
        self.timer_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")
        zoom = float(draft.get("pdf_zoom", self.settings_repo.get_ui_state("queue_pdf_zoom", "1.0") or "1.0"))
        self.pdf.set_zoom(max(0.25, min(4.0, zoom)))
        self.pdf.set_page(int(draft.get("pdf_page", self.active_unit.start_page)), tuple(draft.get("pdf_location", (0, 0))))

    def refresh(self):
        due_units = self.review_repo.due_units(iso_utc(now_utc()))
        source_modes = {s.id: s.learning_mode for s in self.source_repo.list_sources()}
        strict_sources = {sid for sid, mode in source_modes.items() if mode == "strict"}
        available_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        plan = plan_session_queue(
            due_units,
            available_minutes,
            self._estimate_review_seconds,
            strict_progression_sources=strict_sources,
            source_id_of=lambda u: int(u.source_id),
        )
        self.units = plan.selected_units
        self.list.clear()
        self.source_path_cache = {}
        self.display_units = []
        self._queue_last_pdf_page = 1
        suggestion_extra = f" · {len(plan.suggested_units)} progression suggestion(s)" if plan.suggested_units else ""
        self.queue_banner.setText(
            f"Showing {len(self.units)}/{len(due_units)} due units · "
            f"Projected {plan.projected_minutes:.1f} min"
            + (f" · {plan.overflow_count} deferred" if plan.overflow_count else "")
            + suggestion_extra
        )
        existing_ids = set()
        for u in self.units:
            est_seconds = self._estimate_review_seconds(u)
            retention = self._estimate_retention(u)
            tile = self._build_queue_tile(u, est_seconds, retention)
            item = QListWidgetItem()
            self.list.addItem(item)
            self.list.setItemWidget(item, tile)
            self.display_units.append((u, None))
            existing_ids.add(int(u.unit_id))
            if u.source_id not in self.source_path_cache:
                s = self.source_repo.get(u.source_id)
                if s:
                    self.source_path_cache[u.source_id] = s.file_path
                    self.pdf.prime_path(s.file_path)

        for sug in plan.suggested_units:
            if int(sug.unit.unit_id) in existing_ids:
                continue
            est_seconds = max(1.0, sug.estimated_minutes * 60.0)
            tile = self._build_queue_tile(
                sug.unit,
                est_seconds,
                self._estimate_retention(sug.unit),
                progression_reason=sug.reason,
            )
            item = QListWidgetItem()
            self.list.addItem(item)
            self.list.setItemWidget(item, tile)
            self.display_units.append((sug.unit, sug.reason))

        self._relayout_queue_tiles()
        if self.list.count() > 0:
            self.list.setCurrentRow(0)
            self._refresh_queue_doc_progress()
        else:
            self.active_unit = None
            self.title.setText("No unit selected")
            self.queue_doc_progress.set_data([], total_pages=1, current_page=1)

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
            tile.setFixedWidth(tile_w)
            tile.adjustSize()
            item.setSizeHint(self._queue_tile_size_hint(tile, tile_w))

    def _estimate_review_seconds(self, unit) -> float:
        pages = max(1, (unit.end_page - unit.start_page) + 1)
        fallback_per_page = float(self.settings_repo.get("fallback_review_seconds_per_page", "60"))
        fallback_per_unit = float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
        if int(unit.review_count or 0) == 0:
            return max(1.0, pages * fallback_per_page, fallback_per_unit)

        unit_avg = self.review_repo.avg_elapsed_seconds_for_unit(unit.unit_id)
        if unit_avg is not None:
            return unit_avg

        source_avg = self.review_repo.avg_elapsed_seconds_for_source(unit.source_id)
        if source_avg is not None:
            return source_avg

        global_avg = self.review_repo.avg_elapsed_seconds_global()
        if global_avg is not None:
            return global_avg
        return max(1.0, pages * fallback_per_page, fallback_per_unit)

    def _estimate_retention(self, unit) -> float | None:
        if int(unit.review_count or 0) == 0:
            return None
        row = self.review_repo.unit_by_id(unit.unit_id)
        if not row:
            return None
        return retention_estimate(row, now_utc())

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

    def _build_queue_tile(self, unit, est_seconds: float, retention: float | None, progression_reason: str | None = None) -> QWidget:
        root = QFrame()
        root.setObjectName("queueTile")
        root.setStyleSheet(
            "#queueTile { border: 1px solid #2e3a46; border-radius: 10px; padding: 8px; }"
            "QLabel#tileTitle { font-size: 15px; font-weight: 600; color: #f2f5f7; }"
            "QLabel#tileMeta { color: #9aa7b2; font-size: 11px; }"
            "QLabel#badge { border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #d8e1e8; background: #2b3440; }"
            "QLabel#progressBadge { border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #332400; background: #d8b65a; }"
        )

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
        mins = QLabel(self._format_estimated_time(est_seconds)); mins.setObjectName("badge")
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
        if progression_reason:
            progress_badge = QLabel("needed for progression")
            progress_badge.setObjectName("progressBadge")
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

    def pick_unit(self, idx):
        self._save_current_draft()
        if idx < 0 or idx >= len(self.display_units):
            self.active_unit = None
            self.queue_outline_tree.clear()
            self._queue_outline_items = {}
            self._queue_outline_rows_by_id = {}
            self._queue_outline_child_ids = {}
            self._active_queue_outline_node_id = None
            return
        self.active_unit = self.display_units[idx][0]
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
        self._queue_last_pdf_page = int(self.pdf.view_state().get("page", self.active_unit.start_page))
        self._refresh_queue_doc_progress(self._queue_last_pdf_page)

    def jump_to_active_unit(self):
        if self.active_unit:
            self.pdf.set_page(self.active_unit.start_page)

    def tick(self):
        if self.timer_running:
            self.timer_seconds += 1
            self.timer_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")
        if self.active_unit:
            page = int(self.pdf.view_state().get("page", self._queue_last_pdf_page or 1))
            if page != self._queue_last_pdf_page:
                self._queue_last_pdf_page = page
                self._set_active_queue_outline_by_page(page)
                self._refresh_queue_doc_progress(page)

    def toggle_timer(self):
        self.timer_running = not self.timer_running
        if self.timer_running and not self.started_at:
            self.started_at = now_utc()

    def reset_timer(self):
        self.timer_seconds = 0
        self.started_at = None
        self.timer_running = False
        self.timer_lbl.setText("00:00")
        if self.active_unit and self.active_unit.unit_id in self.unit_drafts:
            self.unit_drafts[self.active_unit.unit_id]["timer_seconds"] = 0
            self.unit_drafts[self.active_unit.unit_id]["timer_running"] = False
            self.unit_drafts[self.active_unit.unit_id]["started_at"] = ""

    def rate(self, rating: str):
        if not self.active_unit:
            return
        now = now_utc()
        unit_row = self.review_repo.unit_by_id(self.active_unit.unit_id)
        res = compute_next(unit_row, rating, now)
        payload = {
            "started_at": iso_utc(self.started_at or now),
            "ended_at": iso_utc(now),
            "elapsed_seconds": self.timer_seconds,
            "rating": rating,
            "pre_note": self.pre.toPlainText(),
            "post_note": self.post.toPlainText(),
            "interval_days": res.interval_days,
            "next_review_at": res.next_review_at,
        }
        count = unit_row["review_count"] + 1
        avg = ((unit_row["avg_rating"] * unit_row["review_count"]) + {"easy": 5, "with_effort": 3, "hard": 2, "skip": 1}[rating]) / count
        unit_stats = {
            "last_review_at": iso_utc(now),
            "next_review_at": res.next_review_at,
            "review_count": count,
            "ease_factor": res.ease_factor,
            "interval_days": res.interval_days,
            "avg_rating": avg,
        }
        self.review_repo.record_review(self.active_unit.unit_id, payload, unit_stats)
        self.unit_drafts.pop(self.active_unit.unit_id, None)
        self.reset_timer(); self.pre.clear(); self.post.clear(); self.refresh()

    def open_history(self):
        if not self.active_unit:
            return
        events = self.review_repo.events_for_unit(self.active_unit.unit_id)
        dlg = ReviewHistoryDialog(events, self.review_repo, self)
        dlg.exec()


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
        form.addRow("Daily target minutes", self.daily)
        form.addRow("Low retention threshold %", self.min_ret)
        form.addRow("Max new units/day", self.new_cap)
        form.addRow("Fallback sec/unit", self.fallback_unit_seconds)
        form.addRow("Fallback sec/page", self.fallback_page_seconds)
        save = QPushButton("Save Settings")
        save.clicked.connect(self.save)
        self.summary = QLabel("")
        lay = QVBoxLayout(self); lay.addLayout(form); lay.addWidget(save); lay.addWidget(self.summary); lay.addStretch()
        self.refresh_summary()

    def save(self):
        self.settings_repo.set("daily_minutes", str(self.daily.value()))
        self.settings_repo.set("min_retention_percent", str(self.min_ret.value()))
        self.settings_repo.set("new_units_cap", str(self.new_cap.value()))
        self.settings_repo.set("fallback_review_seconds_per_unit", str(self.fallback_unit_seconds.value()))
        self.settings_repo.set("fallback_review_seconds_per_page", str(self.fallback_page_seconds.value()))
        self.refresh_summary()
        self.settings_changed.emit()

    def refresh_summary(self):
        due_units = self.review_repo.due_units(iso_utc(now_utc()))
        due_review_minutes = sum(self._estimate_review_seconds(u) for u in due_units) / 60.0
        avg_new_unit_seconds = self.review_repo.avg_elapsed_seconds_global() or float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
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
        pages = max(1, (unit.end_page - unit.start_page) + 1)
        fallback_per_page = float(self.settings_repo.get("fallback_review_seconds_per_page", "60"))
        fallback_per_unit = float(self.settings_repo.get("fallback_review_seconds_per_unit", "90"))
        if int(unit.review_count or 0) == 0:
            return max(1.0, pages * fallback_per_page, fallback_per_unit)

        unit_avg = self.review_repo.avg_elapsed_seconds_for_unit(unit.unit_id)
        if unit_avg is not None:
            return unit_avg

        source_avg = self.review_repo.avg_elapsed_seconds_for_source(unit.source_id)
        if source_avg is not None:
            return source_avg

        global_avg = self.review_repo.avg_elapsed_seconds_global()
        if global_avg is not None:
            return global_avg
        return max(1.0, pages * fallback_per_page, fallback_per_unit)


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
        self.queue = StudyQueuePage(source_repo, review_repo, settings_repo)
        self.sources = SourcesPage(source_repo, outline_repo, review_repo, highlight_repo, settings_repo, pdf_service)
        self.settings = SettingsPage(settings_repo, review_repo)
        tabs.addTab(self.queue, "Study Queue")
        tabs.addTab(self.sources, "Sources")
        tabs.addTab(self.settings, "Settings")
        lay.addWidget(tabs)

        self._sync_delay_ms = 5000
        self._queue_dirty = True
        self._queue_sync_timer = QTimer(self)
        self._queue_sync_timer.setSingleShot(True)
        self._queue_sync_timer.timeout.connect(self._flush_debounced_updates)

        self.sources.library_changed.connect(self.sync_queue_views)
        self.sources.queue_changed.connect(self.request_sync_queue_views)
        self.settings.settings_changed.connect(self.sync_queue_views)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    def sync_queue_views(self):
        if self._queue_sync_timer.isActive():
            self._queue_sync_timer.stop()
        self._queue_dirty = True
        self._flush_now(force_queue=True)

    def request_sync_queue_views(self):
        self._queue_dirty = True
        self._queue_sync_timer.start(self._sync_delay_ms)

    def _flush_now(self, force_queue: bool = False):
        if force_queue or self.tabs.currentIndex() == 0:
            self.queue.refresh()
            self._queue_dirty = False
        self.settings.refresh_summary()

    def _flush_debounced_updates(self):
        self._flush_now(force_queue=False)

    def _on_tab_changed(self, index: int) -> None:
        if index != 0:
            return
        if self._queue_sync_timer.isActive():
            self._queue_sync_timer.stop()
        if self._queue_dirty:
            self._flush_now(force_queue=True)

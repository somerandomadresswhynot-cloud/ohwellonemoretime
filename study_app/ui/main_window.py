from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QApplication,
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
from study_app.ui.dialogs import OutlineEditorDialog, ReviewHistoryDialog, SourceMetadataDialog
from study_app.ui.pdf_viewer import PersistentPdfViewer


class SourcesPage(QWidget):
    open_workspace = Signal(int)
    library_changed = Signal()

    def __init__(self, source_repo: SourceRepo, outline_repo: OutlineRepo, pdf_service: PdfService):
        super().__init__()
        self.source_repo = source_repo
        self.outline_repo = outline_repo
        self.pdf_service = pdf_service
        self.current_source_id = None

        search = QLineEdit()
        search.setPlaceholderText("Search sources...")
        search.textChanged.connect(self.refresh)
        self.search = search
        self.list = QListWidget()
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
        btn_edit = QPushButton("Edit Metadata")
        btn_relink = QPushButton("Relink File")
        btn_archive = QPushButton("Toggle Active")
        btn_delete = QPushButton("Delete Source")
        btn_open.clicked.connect(self._open_current_workspace)
        btn_edit.clicked.connect(self.edit_source)
        btn_relink.clicked.connect(self.relink_source)
        btn_archive.clicked.connect(self.toggle_active)
        btn_delete.clicked.connect(self.delete_source)
        right = QVBoxLayout()
        right.addWidget(self.detail)
        for b in [btn_open, btn_edit, btn_relink, btn_archive, btn_delete]:
            right.addWidget(b)
        right.addStretch()

        split = QSplitter()
        lw = QWidget(); lw.setLayout(left)
        rw = QWidget(); rw.setLayout(right)
        split.addWidget(lw); split.addWidget(rw)
        split.setSizes([700, 300])
        lay = QVBoxLayout(self)
        lay.addWidget(split)
        self.refresh()

    def _open_current_workspace(self, *_):
        if self.current_source_id:
            self.open_workspace.emit(self.current_source_id)

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
            f"Title: {s.title}\nActive: {s.is_active}\nFile: {s.file_path}\nExists: {s.file_exists}\nPages: {s.page_count}"
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
        dlg = SourceMetadataDialog(s.title, s.is_active, self)
        if dlg.exec():
            active = dlg.active_edit.text().strip().lower() in {"y", "yes", "true", "1"}
            self.source_repo.update_metadata(s.id, dlg.title_edit.text().strip() or s.title, active)
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
        self.source_repo.update_metadata(s.id, s.title, not s.is_active)
        self.refresh()
        self.library_changed.emit()

    def delete_source(self):
        if self.current_source_id:
            self.source_repo.delete(self.current_source_id)
            self.current_source_id = None
            self.refresh()
            self.library_changed.emit()


class SourceWorkspace(QMainWindow):
    queue_changed = Signal()
    def __init__(self, source_id: int, source_repo: SourceRepo, outline_repo: OutlineRepo, review_repo: ReviewRepo, highlight_repo: HighlightRepo):
        super().__init__()
        self.source_id = source_id
        self.source_repo = source_repo
        self.outline_repo = outline_repo
        self.review_repo = review_repo
        self.highlight_repo = highlight_repo
        self.setWindowTitle("Source Workspace")
        root = QWidget(); self.setCentralWidget(root)
        split = QSplitter()

        self.search = QLineEdit(); self.search.setPlaceholderText("Filter outline")
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Outline", "Queue"])
        self.tree.itemSelectionChanged.connect(self.on_item_select)
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.jump_to_selected())
        btn_all = QPushButton("Enable All")
        btn_none = QPushButton("Disable All")
        btn_edit = QPushButton("Edit Outline Text")
        btn_all.clicked.connect(lambda: self._bulk(True))
        btn_none.clicked.connect(lambda: self._bulk(False))
        btn_edit.clicked.connect(self.edit_outline)
        left_l = QVBoxLayout(); left_l.addWidget(self.search); left_l.addWidget(self.tree); left_l.addWidget(btn_all); left_l.addWidget(btn_none); left_l.addWidget(btn_edit)
        self.search.textChanged.connect(self.refresh_tree)

        self.pdf = PersistentPdfViewer()
        self.pdf.set_selection_menu_handler(self.open_selection_menu)
        self.zoom = QSpinBox(); self.zoom.setRange(50, 250); self.zoom.setValue(100)
        self.zoom.valueChanged.connect(lambda v: self.pdf.set_zoom(v / 100))
        btn_jump = QPushButton("Jump To Selected Unit")
        btn_jump.clicked.connect(self.jump_to_selected)
        btn_hl = QPushButton("Add Highlight from Clipboard")
        btn_hl.clicked.connect(self.add_highlight_from_clipboard)
        self.page_label = QLabel("Page: -")
        c_l = QVBoxLayout(); c_l.addWidget(self.zoom); c_l.addWidget(btn_jump); c_l.addWidget(btn_hl); c_l.addWidget(self.page_label); c_l.addWidget(self.pdf)

        self.insights = QLabel()
        self.insights.setWordWrap(True)
        self.unit_hl_list = QListWidget()
        self.source_hl_tree = QTreeWidget(); self.source_hl_tree.setHeaderLabels(["Page", "Context", "Quote"])
        self.source_hl_tree.itemDoubleClicked.connect(self.on_source_highlight_double_clicked)
        tabs = QTabWidget()
        tab_ins = QWidget(); l1 = QVBoxLayout(tab_ins); l1.addWidget(self.insights); l1.addStretch()
        tab_unit = QWidget(); l2 = QVBoxLayout(tab_unit); l2.addWidget(self.unit_hl_list)
        self.unit_hl_list.itemDoubleClicked.connect(self.edit_unit_highlight)
        tab_src = QWidget(); l3 = QVBoxLayout(tab_src); l3.addWidget(self.source_hl_tree)
        tabs.addTab(tab_ins, "Insights")
        tabs.addTab(tab_unit, "Unit Highlights")
        tabs.addTab(tab_src, "Source Highlights")
        right_l = QVBoxLayout(); right_l.addWidget(tabs); right_l.addStretch()

        lw = QWidget(); lw.setLayout(left_l)
        cw = QWidget(); cw.setLayout(c_l)
        rw = QWidget(); rw.setLayout(right_l)
        split.addWidget(lw); split.addWidget(cw); split.addWidget(rw)
        split.setSizes([300, 700, 320])
        lay = QHBoxLayout(root); lay.addWidget(split)

        self._selected_node = None
        self.load_source()

    def load_source(self):
        self.source = self.source_repo.get(self.source_id)
        self.pdf.load_if_needed(self.source.file_path)
        self.pdf.set_fit_mode()
        self.refresh_tree()
        self.refresh_insights()
        self.refresh_highlights()

    def refresh_tree(self):
        filt = self.search.text().strip().lower()
        rows = self.outline_repo.nodes_for_source(self.source_id)
        self.tree.blockSignals(True)
        self.tree.clear()
        id_to_item = {}
        for r in rows:
            if filt and filt not in r["title"].lower():
                continue
            txt = r["title"]
            if r["start_page"]:
                txt += f" [{r['start_page']}-{r['end_page']}]"
            item = QTreeWidgetItem([txt, "on" if r["queue_enabled"] else "off"])
            item.setData(0, 256, r["id"])
            item.setData(0, 257, r["start_page"])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setCheckState(1, Qt.Checked if r["queue_enabled"] else Qt.Unchecked)
            pid = r["parent_id"]
            if pid and pid in id_to_item:
                id_to_item[pid].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            id_to_item[r["id"]] = item
        self.tree.expandAll()
        self.tree.blockSignals(False)

    def on_item_select(self):
        items = self.tree.selectedItems()
        if not items:
            return
        it = items[0]
        self._selected_node = it.data(0, 256)
        page = it.data(0, 257)
        if page:
            self.page_label.setText(f"Page: {page}")
        self.refresh_insights()
        self.refresh_highlights()

    def on_item_changed(self, item, col):
        if col != 1:
            return
        enabled = item.checkState(1) == Qt.Checked
        self.outline_repo.set_queue_enabled(item.data(0, 256), enabled)
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

    def refresh_insights(self):
        units = self.review_repo.source_units(self.source_id)
        now = datetime.utcnow()
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
        self.highlight_repo.add_highlight(self.source_id, page, quote, note, "#2d9cdb")
        self.refresh_highlights()

    def _color_actions(self):
        return [
            ("Blue", "#2d9cdb"),
            ("Purple", "#8b5cf6"),
            ("Green", "#27ae60"),
            ("Yellow", "#f1c40f"),
            ("Red", "#e74c3c"),
        ]

    def open_selection_menu(self, global_pos, selected_text: str, page: int) -> None:
        quote = (selected_text or "").strip()
        if not quote:
            return
        menu = QMenu(self)
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
        self.highlight_repo.add_highlight(self.source_id, page, quote, "", color)
        self.refresh_highlights()

    def _remove_highlight(self, highlight_id: int) -> None:
        self.highlight_repo.delete_highlight(highlight_id)
        self.refresh_highlights()

    def on_source_highlight_double_clicked(self, item, _col):
        hid = item.data(0, 258)
        page = item.data(0, 256)
        if page:
            self.pdf.set_page(int(page))
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
            self.pdf.set_page(int(page))
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
        group_nodes = {}
        for h in self.highlight_repo.list_source_highlights(self.source_id):
            ctx = h["unit_title"] if h["unit_title"] else "(no unit)"
            if ctx not in group_nodes:
                parent = QTreeWidgetItem(["", ctx, ""])
                self.source_hl_tree.addTopLevelItem(parent)
                group_nodes[ctx] = parent
            quote = h["quote_text"][:120]
            if h["note"]:
                quote = f"{quote}   📝 {h['note'][:70]}"
            item = QTreeWidgetItem([str(h["page"]), ctx, quote])
            item.setData(0, 256, h["page"])
            item.setData(0, 258, h["id"])
            item.setData(0, 259, h["note"] or "")
            color = QColor(h["color"] or "#2d9cdb")
            item.setForeground(0, QBrush(color))
            item.setForeground(2, QBrush(color))
            group_nodes[ctx].addChild(item)
        self.source_hl_tree.expandAll()


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

        self.list = QListWidget()
        self.list.setSpacing(6)
        self.list.currentRowChanged.connect(self.pick_unit)
        self.list.itemDoubleClicked.connect(lambda *_: self.jump_to_active_unit())
        self.queue_banner = QLabel("")
        self.queue_banner.setWordWrap(True)
        left = QVBoxLayout(); left.addWidget(QLabel("Due Units")); left.addWidget(self.queue_banner); left.addWidget(self.list)

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

        right.addWidget(self.pdf)

        corner_row = QHBoxLayout()
        corner_row.addStretch()
        self.resize_corner = CornerResizeHandle(self._resize_pdf_by_delta)
        corner_row.addWidget(self.resize_corner)
        right.addLayout(corner_row)
        right.addSpacing(90)
        right.addStretch()

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
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
                self.started_at = datetime.fromisoformat(started)
            except Exception:
                self.started_at = None
        self.timer_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")
        zoom = float(draft.get("pdf_zoom", self.settings_repo.get_ui_state("queue_pdf_zoom", "1.0") or "1.0"))
        self.pdf.set_zoom(max(0.25, min(4.0, zoom)))
        self.pdf.set_page(int(draft.get("pdf_page", self.active_unit.start_page)), tuple(draft.get("pdf_location", (0, 0))))

    def refresh(self):
        due_units = self.review_repo.due_units(datetime.utcnow().isoformat(timespec="seconds"))
        available_minutes = int(self.settings_repo.get("daily_minutes", "90"))
        plan = plan_session_queue(due_units, available_minutes, self._estimate_review_seconds)
        self.units = plan.selected_units
        self.list.clear()
        self.source_path_cache = {}
        self.queue_banner.setText(
            f"Showing {len(self.units)}/{len(due_units)} due units · "
            f"Projected {plan.projected_minutes:.1f} min"
            + (f" · {plan.overflow_count} deferred" if plan.overflow_count else "")
        )
        for u in self.units:
            est_seconds = self._estimate_review_seconds(u)
            retention = self._estimate_retention(u)
            item = QListWidgetItem()
            item.setSizeHint(self._queue_tile_size_hint())
            self.list.addItem(item)
            self.list.setItemWidget(item, self._build_queue_tile(u, est_seconds, retention))
            if u.source_id not in self.source_path_cache:
                s = self.source_repo.get(u.source_id)
                if s:
                    self.source_path_cache[u.source_id] = s.file_path
                    self.pdf.prime_path(s.file_path)
        if self.list.count() > 0:
            self.list.setCurrentRow(0)
        else:
            self.active_unit = None
            self.title.setText("No unit selected")

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
        return retention_estimate(row, datetime.utcnow())

    def _queue_tile_size_hint(self):
        return QSize(260, 96)

    def _build_queue_tile(self, unit, est_seconds: float, retention: float | None) -> QWidget:
        root = QFrame()
        root.setObjectName("queueTile")
        root.setStyleSheet(
            "#queueTile { border: 1px solid #2e3a46; border-radius: 10px; padding: 8px; }"
            "QLabel#tileTitle { font-size: 15px; font-weight: 600; color: #f2f5f7; }"
            "QLabel#tileMeta { color: #9aa7b2; font-size: 11px; }"
            "QLabel#badge { border-radius: 8px; padding: 2px 8px; font-size: 10px; color: #d8e1e8; background: #2b3440; }"
        )

        lay = QVBoxLayout(root)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        title = QLabel(unit.title)
        title.setObjectName("tileTitle")
        title.setWordWrap(True)

        meta = QLabel(f"{unit.source_title} · reviews: {unit.review_count}")
        meta.setObjectName("tileMeta")

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
        badge_row.addStretch()

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

    def pick_unit(self, idx):
        self._save_current_draft()
        if idx < 0 or idx >= len(self.units):
            self.active_unit = None
            return
        self.active_unit = self.units[idx]
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
        self.pdf.set_page(self.active_unit.start_page)
        self._load_draft_for_active()

    def jump_to_active_unit(self):
        if self.active_unit:
            self.pdf.set_page(self.active_unit.start_page)

    def tick(self):
        if self.timer_running:
            self.timer_seconds += 1
            self.timer_lbl.setText(f"{self.timer_seconds//60:02d}:{self.timer_seconds%60:02d}")

    def toggle_timer(self):
        self.timer_running = not self.timer_running
        if self.timer_running and not self.started_at:
            self.started_at = datetime.utcnow()

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
        now = datetime.utcnow()
        unit_row = self.review_repo.unit_by_id(self.active_unit.unit_id)
        res = compute_next(unit_row, rating, now)
        payload = {
            "started_at": (self.started_at or now).isoformat(timespec="seconds"),
            "ended_at": now.isoformat(timespec="seconds"),
            "elapsed_seconds": self.timer_seconds,
            "rating": rating,
            "pre_note": self.pre.toPlainText(),
            "post_note": self.post.toPlainText(),
            "interval_days": res.interval_days,
            "next_review_at": res.next_review_at,
        }
        self.review_repo.add_event(self.active_unit.unit_id, payload)
        count = unit_row["review_count"] + 1
        avg = ((unit_row["avg_rating"] * unit_row["review_count"]) + {"easy": 5, "with_effort": 3, "hard": 2, "skip": 1}[rating]) / count
        self.review_repo.update_unit_stats(self.active_unit.unit_id, {
            "last_review_at": now.isoformat(timespec="seconds"),
            "next_review_at": res.next_review_at,
            "review_count": count,
            "ease_factor": res.ease_factor,
            "interval_days": res.interval_days,
            "avg_rating": avg,
        })
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
        due_units = self.review_repo.due_units(datetime.utcnow().isoformat(timespec="seconds"))
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
        self.queue = StudyQueuePage(source_repo, review_repo, settings_repo)
        self.sources = SourcesPage(source_repo, outline_repo, pdf_service)
        self.settings = SettingsPage(settings_repo, review_repo)
        tabs.addTab(self.queue, "Study Queue")
        tabs.addTab(self.sources, "Sources")
        tabs.addTab(self.settings, "Settings")
        lay.addWidget(tabs)

        self.sources.open_workspace.connect(self.open_workspace)
        self.sources.library_changed.connect(self.sync_queue_views)
        self.settings.settings_changed.connect(self.sync_queue_views)
        self._workspace_windows = []

    def sync_queue_views(self):
        self.queue.refresh()
        self.settings.refresh_summary()

    def open_workspace(self, source_id: int):
        win = SourceWorkspace(source_id, self.sources.source_repo, self.sources.outline_repo, self.queue.review_repo, self.highlight_repo)
        win.queue_changed.connect(self.sync_queue_views)
        win.show()
        self._workspace_windows.append(win)

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)
from PySide6.QtGui import QTextOption

from study_app.services.outline_service import parse_outline_text


class SourceMetadataDialog(QDialog):
    def __init__(self, title: str, is_active: bool, learning_mode: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Source")
        self.title_edit = QLineEdit(title)
        self.active_edit = QLineEdit("yes" if is_active else "no")
        self.learning_mode_edit = QLineEdit("strict" if learning_mode == "strict" else "any")
        form = QFormLayout()
        form.addRow("Title", self.title_edit)
        form.addRow("Active (yes/no)", self.active_edit)
        form.addRow("Learning order (any/strict)", self.learning_mode_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)


class OutlineEditorDialog(QDialog):
    def __init__(self, outline_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Plain-text Outline Editor")
        self.resize(700, 500)
        self.editor = QTextEdit()
        self.editor.setPlainText(outline_text)
        self.editor.setAcceptRichText(False)
        self.editor.setLineWrapMode(QTextEdit.NoWrap)
        self.editor.setWordWrapMode(QTextOption.NoWrap)
        self.errors = QLabel("")
        self.errors.setStyleSheet("color:#ff8ca1")
        btn_apply = QPushButton("Validate + Apply")
        btn_apply.clicked.connect(self._validate)
        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        btns.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Syntax: ## Heading [p10-12]"))
        lay.addWidget(self.editor)
        lay.addWidget(self.errors)
        row = QHBoxLayout()
        row.addWidget(btn_apply)
        row.addWidget(btns)
        lay.addLayout(row)
        self.parsed_entries = None

    def _validate(self):
        entries, errs = parse_outline_text(self.editor.toPlainText())
        if errs:
            self.errors.setText("\n".join(f"Line {e.line_no}: {e.message}" for e in errs[:12]))
            return
        if not entries:
            self.errors.setText("Outline cannot be empty")
            return
        self.parsed_entries = entries
        self.accept()


class ReviewHistoryDialog(QDialog):
    def __init__(self, events: list, review_repo, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review History")
        self.resize(680, 500)
        self.review_repo = review_repo
        self.events = events
        self.list = QListWidget()
        self.pre = QTextEdit()
        self.post = QTextEdit()
        self._load()
        self.list.currentRowChanged.connect(self._on_pick)
        save_btn = QPushButton("Save Notes")
        del_btn = QPushButton("Delete Event")
        save_btn.clicked.connect(self._save)
        del_btn.clicked.connect(self._delete)
        lay = QVBoxLayout(self)
        lay.addWidget(self.list)
        lay.addWidget(QLabel("Pre-note"))
        lay.addWidget(self.pre)
        lay.addWidget(QLabel("Post-note"))
        lay.addWidget(self.post)
        row = QHBoxLayout()
        row.addWidget(save_btn)
        row.addWidget(del_btn)
        lay.addLayout(row)

    def _load(self):
        self.list.clear()
        for ev in self.events:
            item = QListWidgetItem(f"#{ev['id']} {ev['ended_at']} {ev['rating']} {ev['elapsed_seconds']}s")
            item.setData(256, ev)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_pick(self, idx: int):
        if idx < 0:
            return
        ev = self.list.item(idx).data(256)
        self.pre.setText(ev["pre_note"])
        self.post.setText(ev["post_note"])

    def _save(self):
        idx = self.list.currentRow()
        if idx < 0:
            return
        ev = self.list.item(idx).data(256)
        self.review_repo.edit_event_notes(ev["id"], self.pre.toPlainText(), self.post.toPlainText())
        QMessageBox.information(self, "Saved", "Notes updated.")

    def _delete(self):
        idx = self.list.currentRow()
        if idx < 0:
            return
        ev = self.list.item(idx).data(256)
        self.review_repo.soft_delete_event(ev["id"])
        QMessageBox.information(self, "Deleted", "Event marked deleted.")
        self.events = [e for e in self.events if e["id"] != ev["id"]]
        self._load()

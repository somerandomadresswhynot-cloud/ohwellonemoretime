from __future__ import annotations

import html

from PySide6.QtCore import Qt, QUrl
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
    QTextBrowser,
    QTextEdit,
    QStackedWidget,
    QVBoxLayout,
)
from PySide6.QtGui import QAction, QTextCursor, QTextDocument, QTextOption

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


class RecallNoteDialog(QDialog):
    def __init__(self, title: str, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        self.editor = QTextEdit()
        self.editor.setPlainText(text or "")
        self.editor.setAcceptRichText(False)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.editor)
        lay.addWidget(buttons)

    def value(self) -> str:
        return self.editor.toPlainText()


_CLOZE_OPEN = "{{c::"
_CLOZE_CLOSE = "}}"


def _extract_cloze_segments(markdown_text: str) -> tuple[str, list[str]]:
    text = markdown_text or ""
    parts: list[str] = []
    clozes: list[str] = []
    idx = 0
    while idx < len(text):
        start = text.find(_CLOZE_OPEN, idx)
        if start < 0:
            parts.append(text[idx:])
            break
        parts.append(text[idx:start])
        value_start = start + len(_CLOZE_OPEN)
        end = text.find(_CLOZE_CLOSE, value_start)
        if end < 0:
            parts.append(text[start:])
            break
        cloze_value = text[value_start:end]
        if _CLOZE_OPEN in cloze_value:
            parts.append(text[start:end + len(_CLOZE_CLOSE)])
        else:
            token = f"CLOZE_TOKEN_{len(clozes)}"
            parts.append(token)
            clozes.append(cloze_value)
        idx = end + len(_CLOZE_CLOSE)
    return "".join(parts), clozes


def _cloze_validation_messages(markdown_text: str) -> list[str]:
    text = markdown_text or ""
    messages: list[str] = []
    open_count = text.count(_CLOZE_OPEN)
    close_count = text.count(_CLOZE_CLOSE)
    if open_count > close_count:
        messages.append("Unclosed cloze marker found. Use '{{c::...}}'.")
    if close_count > open_count:
        messages.append("Extra closing cloze marker found ('}}').")
    idx = 0
    while idx < len(text):
        start = text.find(_CLOZE_OPEN, idx)
        if start < 0:
            break
        body_start = start + len(_CLOZE_OPEN)
        end = text.find(_CLOZE_CLOSE, body_start)
        if end < 0:
            break
        body = text[body_start:end]
        if _CLOZE_OPEN in body:
            messages.append("Nested cloze is not supported; inner markers are ignored.")
            break
        idx = end + len(_CLOZE_CLOSE)
    return messages


class HintMarkdownDialog(QDialog):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hint")
        self.resize(760, 620)
        self.editor = QTextEdit()
        self.editor.setAcceptRichText(False)
        self.editor.setPlainText(text or "")
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._open_editor_context_menu)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.anchorClicked.connect(self._on_anchor_clicked)
        self._preview_scroll_value = 0
        self._editor_scroll_value = 0
        self._edit_mode = True
        self._revealed_cloze_indexes: set[int] = set()
        self._cloze_values: list[str] = []
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color:#9aa7b2;")
        self._validation_label = QLabel("")
        self._validation_label.setWordWrap(True)
        self._validation_label.setStyleSheet("color:#ffb27f;")

        self.history_hint = QLabel("Cloze: select text and right-click → Make Cloze. Right-click inside a cloze → Remove Cloze.")
        self.history_hint.setStyleSheet("color:#9aa7b2;")
        reveal_all_btn = QPushButton("Reveal All")
        hide_all_btn = QPushButton("Hide All")
        toggle_all_btn = QPushButton("Toggle All")
        self.mode_btn = QPushButton("Switch to Render Markdown")
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(4)
        self._format_buttons: list[QPushButton] = []
        for label, handler in [
            ("H1", lambda: self._prefix_lines("# ")),
            ("H2", lambda: self._prefix_lines("## ")),
            ("B", lambda: self._wrap_selection("**", "**")),
            ("I", lambda: self._wrap_selection("*", "*")),
            ("Code", lambda: self._wrap_selection("`", "`")),
            ("Quote", lambda: self._prefix_lines("> ")),
            ("List", lambda: self._prefix_lines("- ")),
            ("Link", self._insert_link_template),
        ]:
            btn = QPushButton(label)
            btn.setFixedHeight(24)
            btn.clicked.connect(handler)
            self._format_buttons.append(btn)
            self.toolbar.addWidget(btn)
        self.toolbar.addStretch()
        reveal_all_btn.clicked.connect(self._reveal_all_clozes)
        hide_all_btn.clicked.connect(self._hide_all_clozes)
        toggle_all_btn.clicked.connect(self._toggle_all_clozes)
        self.mode_btn.clicked.connect(self._toggle_mode)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.editor.textChanged.connect(self._refresh_preview)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.preview)
        lay = QVBoxLayout(self)
        lay.addWidget(self.mode_btn, 0, Qt.AlignLeft)
        lay.addLayout(self.toolbar)
        lay.addWidget(self.stack, 1)
        count_row = QHBoxLayout()
        count_row.addWidget(self._count_label)
        count_row.addStretch()
        count_row.addWidget(reveal_all_btn)
        count_row.addWidget(hide_all_btn)
        count_row.addWidget(toggle_all_btn)
        lay.addLayout(count_row)
        lay.addWidget(self._validation_label)
        lay.addWidget(self.history_hint)
        lay.addWidget(buttons)
        self._refresh_preview()
        self._set_mode(edit_mode=True)

    def _open_editor_context_menu(self, pos) -> None:
        self._apply_context_menu_cursor(pos)
        menu = self.editor.createStandardContextMenu()
        make_action = QAction("Make Cloze", self)
        make_action.triggered.connect(self._make_cloze_from_selection)
        make_action.setEnabled(self.editor.textCursor().hasSelection())
        menu.addAction(make_action)

        cloze_bounds = self._cloze_bounds_at_cursor()
        if cloze_bounds is not None:
            remove_action = QAction("Remove Cloze", self)
            remove_action.triggered.connect(lambda: self._remove_cloze(*cloze_bounds))
            menu.addAction(remove_action)
        menu.exec(self.editor.mapToGlobal(pos))

    def _apply_context_menu_cursor(self, pos) -> None:
        clicked_cursor = self.editor.cursorForPosition(pos)
        existing = self.editor.textCursor()
        if existing.hasSelection():
            start = existing.selectionStart()
            end = existing.selectionEnd()
            click_pos = clicked_cursor.position()
            if start <= click_pos <= end:
                return
        self.editor.setTextCursor(clicked_cursor)

    def _cloze_bounds_at_cursor(self) -> tuple[int, int] | None:
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        pos = cursor.position()
        idx = 0
        while idx < len(text):
            start = text.find(_CLOZE_OPEN, idx)
            if start < 0:
                break
            body_start = start + len(_CLOZE_OPEN)
            end = text.find(_CLOZE_CLOSE, body_start)
            if end < 0:
                break
            close_end = end + len(_CLOZE_CLOSE)
            if start <= pos <= close_end:
                return start, close_end
            idx = close_end
        return None

    def _make_cloze_from_selection(self) -> None:
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        if end <= start:
            return
        selected = text[start:end]
        if not selected.strip():
            return
        if _CLOZE_OPEN in selected or _CLOZE_CLOSE in selected:
            return
        cursor.beginEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(f"{{{{c::{selected}}}}}")
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)

    def _remove_cloze(self, start: int, end: int) -> None:
        text = self.editor.toPlainText()
        fragment = text[start:end]
        if not (fragment.startswith(_CLOZE_OPEN) and fragment.endswith(_CLOZE_CLOSE)):
            return
        replacement = fragment[len(_CLOZE_OPEN):-len(_CLOZE_CLOSE)]
        cursor = self.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.insertText(replacement)
        self.editor.setTextCursor(cursor)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        if url.scheme() != "cloze":
            return
        try:
            idx = int(url.path().strip("/"))
        except Exception:
            return
        if idx in self._revealed_cloze_indexes:
            self._revealed_cloze_indexes.remove(idx)
        else:
            self._revealed_cloze_indexes.add(idx)
        self._refresh_preview()

    def _reveal_all_clozes(self) -> None:
        self._revealed_cloze_indexes = set(range(len(self._cloze_values)))
        self._refresh_preview()

    def _hide_all_clozes(self) -> None:
        self._revealed_cloze_indexes = set()
        self._refresh_preview()

    def _toggle_all_clozes(self) -> None:
        if not self._cloze_values:
            return
        if len(self._revealed_cloze_indexes) >= len(self._cloze_values):
            self._revealed_cloze_indexes = set()
        else:
            self._revealed_cloze_indexes = set(range(len(self._cloze_values)))
        self._refresh_preview()

    def _toggle_mode(self) -> None:
        self._set_mode(edit_mode=not self._edit_mode)

    def _wrap_selection(self, prefix: str, suffix: str) -> None:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().replace("\u2029", "\n")
            cursor.insertText(f"{prefix}{text}{suffix}")
        else:
            cursor.insertText(f"{prefix}{suffix}")
            cursor.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, len(suffix))
            self.editor.setTextCursor(cursor)

    def _prefix_lines(self, prefix: str) -> None:
        cursor = self.editor.textCursor()
        text = self.editor.toPlainText()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        line_start = text.rfind("\n", 0, start) + 1
        line_end = text.find("\n", end)
        if line_end < 0:
            line_end = len(text)
        block = text[line_start:line_end]
        lines = block.split("\n")
        updated = "\n".join(prefix + line if line.strip() else line for line in lines)
        cursor.beginEditBlock()
        cursor.setPosition(line_start)
        cursor.setPosition(line_end, QTextCursor.KeepAnchor)
        cursor.insertText(updated)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)

    def _insert_link_template(self) -> None:
        cursor = self.editor.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n").strip() if cursor.hasSelection() else "text"
        cursor.insertText(f"[{selected}](https://)")

    def _set_mode(self, edit_mode: bool) -> None:
        if self._edit_mode and not edit_mode:
            self._editor_scroll_value = self.editor.verticalScrollBar().value()
            self._preview_scroll_value = self.preview.verticalScrollBar().value()
        elif (not self._edit_mode) and edit_mode:
            self._preview_scroll_value = self.preview.verticalScrollBar().value()
            self._editor_scroll_value = self.editor.verticalScrollBar().value()
        self._edit_mode = bool(edit_mode)
        self.stack.setCurrentWidget(self.editor if self._edit_mode else self.preview)
        self.mode_btn.setText("Switch to Render Markdown" if self._edit_mode else "Switch to Edit Markdown")
        for btn in self._format_buttons:
            btn.setEnabled(self._edit_mode)
        if self._edit_mode:
            self.editor.verticalScrollBar().setValue(self._editor_scroll_value)
        else:
            self.preview.verticalScrollBar().setValue(self._preview_scroll_value)

    def _refresh_preview(self) -> None:
        source = self.editor.toPlainText()
        markdown_with_tokens, self._cloze_values = _extract_cloze_segments(source)
        self._revealed_cloze_indexes = {idx for idx in self._revealed_cloze_indexes if idx < len(self._cloze_values)}
        warning_messages = _cloze_validation_messages(source)
        self._validation_label.setText("\n".join(warning_messages))
        preview_scroll = self.preview.verticalScrollBar().value()
        editor_scroll = self.editor.verticalScrollBar().value()
        doc = QTextDocument()
        doc.setMarkdown(markdown_with_tokens)
        rendered = doc.toHtml()
        for idx, value in enumerate(self._cloze_values):
            token = f"CLOZE_TOKEN_{idx}"
            if idx in self._revealed_cloze_indexes:
                label = html.escape(value)
            else:
                label = "▇▇▇"
            rendered = rendered.replace(token, f'<a href="cloze://{idx}">{label}</a>')
        hidden = max(0, len(self._cloze_values) - len(self._revealed_cloze_indexes))
        shown = len(self._revealed_cloze_indexes)
        self._count_label.setText(f"Clozes: {len(self._cloze_values)} total · {hidden} hidden · {shown} shown")
        self.preview.setHtml(rendered)
        self._preview_scroll_value = preview_scroll
        self._editor_scroll_value = editor_scroll
        self.preview.verticalScrollBar().setValue(self._preview_scroll_value)
        self.editor.verticalScrollBar().setValue(self._editor_scroll_value)

    def value(self) -> str:
        return self.editor.toPlainText()

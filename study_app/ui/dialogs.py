from __future__ import annotations

import json

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QFrame,
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
from PySide6.QtWebEngineWidgets import QWebEngineView

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
        self.resize(920, 700)
        self._value = text or ""
        self.web = QWebEngineView()
        self.mode_btn = QPushButton("Render")
        self.make_cloze_btn = QPushButton("Cloze")
        self.reveal_all_btn = QPushButton("Show")
        self.hide_all_btn = QPushButton("Hide")
        self.mode_btn.setToolTip("Toggle rendered markdown preview")
        self.make_cloze_btn.setToolTip("Wrap selected text as {{c::...}}")
        self.reveal_all_btn.setToolTip("Reveal all clozes in rendered preview")
        self.hide_all_btn.setToolTip("Hide all clozes in rendered preview")
        self.mode_btn.clicked.connect(lambda: self.web.page().runJavaScript("window.togglePreviewMode();"))
        self.make_cloze_btn.clicked.connect(lambda: self.web.page().runJavaScript("window.wrapSelectionCloze();"))
        self.reveal_all_btn.clicked.connect(lambda: self.web.page().runJavaScript("window.setAllClozes(true);"))
        self.hide_all_btn.clicked.connect(lambda: self.web.page().runJavaScript("window.setAllClozes(false);"))
        self.setStyleSheet(
            "QDialog{background:#0b1530;color:#dbe4ef;}"
            "QFrame#hintToolbarGroup{background:#0e1b38;border:1px solid #1f3a66;border-radius:8px;}"
            "QLabel#groupTitle{color:#8fb4f5;font-size:11px;font-weight:600;padding:2px 0;}"
            "QPushButton{background:#223761;border:1px solid #345389;color:#e8f0ff;padding:6px 12px;border-radius:6px;}"
            "QPushButton:hover{background:#2a4678;border-color:#4063a0;}"
            "QPushButton:pressed{background:#1b2d4e;}"
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save_btn = buttons.button(QDialogButtonBox.Save)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        if save_btn:
            save_btn.setText("Save Hint")
            save_btn.setStyleSheet("background:#1e7f68;border:1px solid #2ea387;color:#effff9;font-weight:600;padding:6px 14px;border-radius:6px;")
        if cancel_btn:
            cancel_btn.setStyleSheet("background:#1f3155;border:1px solid #35527f;color:#d8e7ff;padding:6px 14px;border-radius:6px;")
        buttons.accepted.connect(self._save_from_web)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        control_row = QHBoxLayout()
        control_row.setSpacing(10)
        control_row.setContentsMargins(0, 0, 0, 0)

        view_group = QFrame()
        view_group.setObjectName("hintToolbarGroup")
        view_lay = QVBoxLayout(view_group)
        view_lay.setContentsMargins(10, 8, 10, 8)
        view_lay.setSpacing(6)
        view_title = QLabel("View")
        view_title.setObjectName("groupTitle")
        view_lay.addWidget(view_title)
        view_lay.addWidget(self.mode_btn)

        cloze_group = QFrame()
        cloze_group.setObjectName("hintToolbarGroup")
        cloze_lay = QVBoxLayout(cloze_group)
        cloze_lay.setContentsMargins(10, 8, 10, 8)
        cloze_lay.setSpacing(6)
        cloze_title = QLabel("Cloze")
        cloze_title.setObjectName("groupTitle")
        cloze_lay.addWidget(cloze_title)
        cloze_btn_row = QHBoxLayout()
        cloze_btn_row.setSpacing(6)
        cloze_btn_row.addWidget(self.make_cloze_btn)
        cloze_btn_row.addWidget(self.reveal_all_btn)
        cloze_btn_row.addWidget(self.hide_all_btn)
        cloze_lay.addLayout(cloze_btn_row)

        control_row.addWidget(view_group, 0)
        control_row.addWidget(cloze_group, 1)
        control_row.addStretch()
        lay.addLayout(control_row)
        lay.addWidget(self.web, 1)
        lay.addWidget(buttons)
        self.web.loadFinished.connect(self._on_loaded)
        self.web.setHtml(_hint_editor_html(), baseUrl=QUrl("https://cdn.jsdelivr.net/"))

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            return
        payload = json.dumps(self._value)
        self.web.page().runJavaScript(f"window.setMarkdown({payload});")

    def _save_from_web(self) -> None:
        self.web.page().runJavaScript("window.getMarkdown();", self._on_markdown_ready)

    def _on_markdown_ready(self, value) -> None:
        self._value = str(value or "")
        self.accept()

    def value(self) -> str:
        return self._value


def _hint_editor_html() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.css">
  <style>
    body { margin:0; background:#0b1530; color:#dbe4ef; font-family:Inter,Segoe UI,Arial,sans-serif; }
    .editor-toolbar { background:#0d1a36; border:1px solid #25406f; border-bottom:0; padding:6px 6px; }
    .editor-toolbar a { color:#dbe8ff !important; }
    .editor-toolbar i { color:#dbe8ff !important; }
    .editor-toolbar a:hover, .editor-toolbar a.active { background:#223a64 !important; border-color:#34558f !important; }
    .editor-toolbar i.separator { border-color:#2f4f84 !important; }
    .CodeMirror { background:#101d3b; color:#e6efff; border:1px solid #25406f; min-height:380px; }
    .CodeMirror-cursor { border-left:1px solid #e6efff !important; }
    .CodeMirror-gutters { background:#0f1b36; border-right:1px solid #223a64; }
    .editor-preview, .editor-preview-side { background:#101d3b; color:#e6efff; }
    .cloze-box { display:inline-block; vertical-align:baseline; white-space:nowrap; overflow:hidden; text-overflow:clip; border-radius:4px; padding:0 4px; cursor:pointer; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .cloze-hidden { background:#33425f; color:transparent; }
    .cloze-shown { background:#1f7a3d; color:#ecffef; }
  </style>
</head>
<body>
  <textarea id="editor-root"></textarea>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.js"></script>
  <script>
    let renderMode = false;
    let editor = new EasyMDE({
      element: document.getElementById('editor-root'),
      spellChecker: false,
      status: false,
      toolbar: [
        'bold', 'italic', 'heading', '|',
        'quote', 'unordered-list', 'ordered-list', '|',
        'link', 'image', 'code', '|',
        'preview', 'side-by-side', 'fullscreen'
      ],
      renderingConfig: { singleLineBreaks: false },
      previewRender: function(text) {
        const replaced = text.replace(/\\{\\{c::([\\s\\S]*?)\\}\\}/g, function(_m, g1) {
          const width = Math.max(3, (g1 || '').length);
          return '<span class=\"cloze-box cloze-hidden\" data-answer=\"' + encodeURIComponent(g1) + '\" data-width=\"' + width + '\" style=\"width:' + width + 'ch\">▇▇▇</span>';
        });
        return marked.parse(replaced);
      }
    });
    document.addEventListener('click', function(ev) {
      const target = ev.target;
      if (!target || !target.classList) return;
      if (target.classList.contains('cloze-hidden')) {
        const answer = decodeURIComponent(target.getAttribute('data-answer') || '');
        const width = target.getAttribute('data-width') || '3';
        target.style.width = width + 'ch';
        target.textContent = answer;
        target.classList.remove('cloze-hidden');
        target.classList.add('cloze-shown');
      } else if (target.classList.contains('cloze-shown')) {
        const width = target.getAttribute('data-width') || '3';
        target.style.width = width + 'ch';
        target.textContent = '▇▇▇';
        target.classList.remove('cloze-shown');
        target.classList.add('cloze-hidden');
      }
    });
    window.togglePreviewMode = function() { editor.togglePreview(); renderMode = !renderMode; };
    window.wrapSelectionCloze = function() {
      const cm = editor.codemirror;
      const selected = cm.getSelection();
      if (!selected || !selected.trim()) return;
      cm.replaceSelection('{{c::' + selected + '}}');
    };
    window.setAllClozes = function(reveal) {
      const nodes = document.querySelectorAll('.cloze-hidden, .cloze-shown');
      for (const n of nodes) {
        const answer = decodeURIComponent(n.getAttribute('data-answer') || '');
        const width = n.getAttribute('data-width') || '3';
        n.style.width = width + 'ch';
        if (reveal) {
          n.textContent = answer;
          n.classList.remove('cloze-hidden');
          n.classList.add('cloze-shown');
        } else {
          n.textContent = '▇▇▇';
          n.classList.remove('cloze-shown');
          n.classList.add('cloze-hidden');
        }
      }
    };
    window.getMarkdown = function() { return editor.value(); };
    window.setMarkdown = function(v) { editor.value(v || ''); };
  </script>
</body>
</html>
"""

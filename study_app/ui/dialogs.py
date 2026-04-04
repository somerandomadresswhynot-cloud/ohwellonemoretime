from __future__ import annotations

import json

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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
from study_app.services.cloze_utils import replace_nth_cloze


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
        self.changed = False
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
            ended = str(ev["ended_at"] or "")
            day = ended[:10] if len(ended) >= 10 else ended
            kind = str(ev["event_kind"] or "")
            grade = str(ev["fsrs_grade"] or "")
            if kind == "fsrs_review":
                label = f"fsrs:{grade or ev['rating']}"
            elif kind:
                label = kind
            else:
                label = str(ev["rating"])
            item = QListWidgetItem(f"#{ev['id']} {day} {label} {ev['elapsed_seconds']}s")
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
        self.changed = True
        QMessageBox.information(self, "Deleted", "Event marked deleted.")
        self.events = [e for e in self.events if e["id"] != ev["id"]]
        self._load()


class RecallNoteDialog(QDialog):
    text_changed = Signal(str)

    def __init__(self, title: str, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        self.editor = QTextEdit()
        self.editor.setPlainText(text or "")
        self.editor.setAcceptRichText(False)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(700)
        self._debounce_timer.timeout.connect(self._emit_debounced_change)
        self.editor.textChanged.connect(lambda: self._debounce_timer.start())
        buttons = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Cancel)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn:
            close_btn.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.editor)
        lay.addWidget(buttons)

    def _emit_debounced_change(self) -> None:
        self.text_changed.emit(self.value())

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


def _replace_nth_cloze(markdown_text: str, cloze_index: int) -> str:
    return replace_nth_cloze(markdown_text, cloze_index)


class HintMarkdownDialog(QDialog):
    keep_on_top_changed = Signal(bool)
    markdown_changed = Signal(str)

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hint")
        self.resize(920, 700)
        self._value = text or ""
        self._is_loaded = False
        self._keep_on_top = True
        self._parent_for_filter = parent if hasattr(parent, "installEventFilter") else None
        self.web = QWebEngineView()
        self.make_cloze_btn = QPushButton("Cloze")
        self.reveal_all_btn = QPushButton("Reveal All")
        self.hide_all_btn = QPushButton("Hide All")
        self.toggle_all_btn = QPushButton("Toggle All")
        self.make_cloze_btn.setToolTip("Wrap selected text as {{c::...}}")
        self.reveal_all_btn.setToolTip("Reveal all clozes in rendered preview")
        self.hide_all_btn.setToolTip("Hide all clozes in rendered preview")
        self.toggle_all_btn.setToolTip("Toggle all clozes in rendered preview")
        self.make_cloze_btn.clicked.connect(self._on_make_cloze_clicked)
        self.reveal_all_btn.clicked.connect(self._on_reveal_all_clicked)
        self.hide_all_btn.clicked.connect(self._on_hide_all_clicked)
        self.toggle_all_btn.clicked.connect(self._on_toggle_all_clicked)
        self.setStyleSheet(
            "QDialog{background:#0b1530;color:#dbe4ef;}"
            "QLabel#hintTitle{color:#e7efff;font-size:13px;font-weight:600;}"
            "QPushButton{background:#223761;border:1px solid #345389;color:#e8f0ff;padding:5px 10px;border-radius:6px;}"
            "QPushButton:hover{background:#2a4678;border-color:#4063a0;}"
            "QPushButton:pressed{background:#1b2d4e;}"
        )
        self._change_poll_timer = QTimer(self)
        self._change_poll_timer.setInterval(900)
        self._change_poll_timer.timeout.connect(self._poll_markdown_change)
        self._last_emitted_value = self._value
        buttons = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Cancel)
        save_btn = buttons.button(QDialogButtonBox.Close)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        if save_btn:
            save_btn.setText("Close")
            save_btn.setStyleSheet("background:#1e7f68;border:1px solid #2ea387;color:#effff9;font-weight:600;padding:6px 14px;border-radius:6px;")
        if cancel_btn:
            cancel_btn.setStyleSheet("background:#1f3155;border:1px solid #35527f;color:#d8e7ff;padding:6px 14px;border-radius:6px;")
        buttons.accepted.connect(self._save_from_web)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Hint Markdown")
        title.setObjectName("hintTitle")
        title_row.addWidget(title)
        self.keep_on_top_check = QCheckBox("Show on top of this app")
        self.keep_on_top_check.setToolTip("Keeps the hint above this app window while enabled.")
        self.keep_on_top_check.setChecked(True)
        self.keep_on_top_check.toggled.connect(self._on_keep_on_top_toggled)
        title_row.addWidget(self.keep_on_top_check)
        title_row.addStretch()
        lay.addLayout(title_row)

        toolbar_row = QHBoxLayout()
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(6)
        toolbar_row.addWidget(self.make_cloze_btn)
        toolbar_row.addWidget(self.reveal_all_btn)
        toolbar_row.addWidget(self.hide_all_btn)
        toolbar_row.addWidget(self.toggle_all_btn)
        toolbar_row.addStretch()
        lay.addLayout(toolbar_row)
        lay.addWidget(self.web, 1)
        lay.addWidget(buttons)
        self.web.loadFinished.connect(self._on_loaded)
        self.web.setHtml(_hint_editor_html(), baseUrl=QUrl("https://cdn.jsdelivr.net/"))
        self._set_parent_filter_enabled(True)

    def closeEvent(self, event) -> None:
        self._set_parent_filter_enabled(False)
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._set_parent_filter_enabled(False)
        super().done(result)

    def eventFilter(self, watched, event):
        if watched is self._parent_for_filter and self._keep_on_top and event.type() == QEvent.WindowActivate:
            self.raise_()
        return super().eventFilter(watched, event)

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            return
        self._is_loaded = True
        payload = json.dumps(self._value)
        self.web.page().runJavaScript(f"window.setMarkdown({payload});")
        self._change_poll_timer.start()

    def _save_from_web(self) -> None:
        self.web.page().runJavaScript("window.getMarkdown();", self._on_markdown_ready)

    def _on_markdown_ready(self, value) -> None:
        self._value = str(value or "")
        if self._value != self._last_emitted_value:
            self._last_emitted_value = self._value
            self.markdown_changed.emit(self._value)
        self.accept()

    def _poll_markdown_change(self) -> None:
        if not self.web:
            return
        self.web.page().runJavaScript("window.getMarkdown();", self._on_polled_markdown_ready)

    def _on_polled_markdown_ready(self, value) -> None:
        current = str(value or "")
        if current == self._last_emitted_value:
            return
        self._last_emitted_value = current
        self.markdown_changed.emit(current)

    def value(self) -> str:
        return self._value

    def set_value(self, text: str) -> None:
        self._value = str(text or "")
        self._last_emitted_value = self._value
        if not self._is_loaded or not self.web:
            return
        payload = json.dumps(self._value)
        self.web.page().runJavaScript(f"window.setMarkdown({payload});")

    def keep_on_top(self) -> bool:
        return bool(self._keep_on_top)

    def set_keep_on_top(self, enabled: bool) -> None:
        checked = bool(enabled)
        self.keep_on_top_check.setChecked(checked)
        self._on_keep_on_top_toggled(checked)

    def _on_keep_on_top_toggled(self, enabled: bool) -> None:
        self._keep_on_top = bool(enabled)
        self.keep_on_top_changed.emit(self._keep_on_top)
        if self._keep_on_top:
            self.raise_()

    def _set_parent_filter_enabled(self, enabled: bool) -> None:
        if not self._parent_for_filter:
            return
        if enabled:
            self._parent_for_filter.installEventFilter(self)
            return
        self._parent_for_filter.removeEventFilter(self)

    def _run_editor_action(self, function_name: str, retries: int = 12) -> None:
        if not self.web:
            return
        fn = str(function_name or "").strip()
        if not fn:
            return
        script = (
            "(function(){"
            f"if (typeof {fn} !== 'function') return false;"
            f"{fn}();"
            "return true;"
            "})();"
        )

        def _callback(ok) -> None:
            if bool(ok):
                return
            if retries <= 0:
                return
            QTimer.singleShot(120, lambda: self._run_editor_action(fn, retries=retries - 1))

        self.web.page().runJavaScript(script, _callback)

    def _on_make_cloze_clicked(self) -> None:
        self._run_editor_action("window.wrapSelectionCloze")

    def _on_reveal_all_clicked(self) -> None:
        self._run_editor_action("window.setAllClozesReveal")

    def _on_hide_all_clicked(self) -> None:
        self._run_editor_action("window.setAllClozesHide")

    def _on_toggle_all_clicked(self) -> None:
        self._run_editor_action("window.toggleAllClozes")

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
    .editor-toolbar a:hover { background:#223a64 !important; border-color:#34558f !important; color:#e6f0ff !important; }
    .editor-toolbar a.active,
    .editor-toolbar button.active {
      background:rgba(143,180,245,0.22) !important;
      border-color:#4a6fae !important;
      box-shadow:none !important;
      color:#d8e7ff !important;
    }
    .editor-toolbar a.active i,
    .editor-toolbar button.active i { color:#d8e7ff !important; }
    .editor-toolbar i.separator { border-color:#2f4f84 !important; }
    .CodeMirror { background:#101d3b; color:#e6efff; border:1px solid #25406f; min-height:380px; }
    .CodeMirror-cursor { border-left:1px solid #e6efff !important; }
    .CodeMirror-gutters { background:#0f1b36; border-right:1px solid #223a64; }
    .CodeMirror-scroll { overflow: hidden !important; }
    .editor-preview, .editor-preview-side { background:#101d3b; color:#e6efff; }
    .editor-preview::-webkit-scrollbar, .editor-preview-side::-webkit-scrollbar { display:none; width:0; height:0; }
    .CodeMirror-scrollbar-filler, .CodeMirror-gutter-filler { display:none !important; }
    .cloze-box { display:inline-block; vertical-align:baseline; white-space:nowrap; overflow:hidden; text-overflow:clip; border-radius:4px; padding:0 4px; cursor:pointer; font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .cloze-hidden { background:#33425f; color:transparent; }
    .cloze-shown { background:#1f7a3d; color:#ecffef; }
    .cloze-menu {
      position:absolute;
      display:none;
      flex-direction:column;
      z-index:9999;
      border:1px solid #345389;
      border-radius:8px;
      overflow:hidden;
      background:#122546;
      box-shadow:0 8px 22px rgba(0,0,0,0.35);
    }
    .cloze-menu button {
      border:0;
      color:#e8f0ff;
      background:#122546;
      text-align:left;
      padding:8px 12px;
      cursor:pointer;
    }
    .cloze-menu button:hover { background:#223a64; }
  </style>
</head>
<body>
  <textarea id="editor-root"></textarea>
  <div id="cloze-menu" class="cloze-menu">
    <button type="button" id="cloze-menu-toggle">Reveal/Hide</button>
    <button type="button" id="cloze-menu-uncloze">Make normal text</button>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/easymde/dist/easymde.min.js"></script>
  <script>
    const measureCanvas = document.createElement('canvas');
    const measureCtx = measureCanvas.getContext('2d');
    const clozeFont = '600 16px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
    const clozeMenu = document.getElementById('cloze-menu');
    const clozeMenuToggle = document.getElementById('cloze-menu-toggle');
    const clozeMenuUncloze = document.getElementById('cloze-menu-uncloze');
    let clozeMenuTarget = null;
    function hideClozeMenu() {
      clozeMenu.style.display = 'none';
      clozeMenuTarget = null;
    }
    function showClozeMenu(target, x, y) {
      clozeMenuTarget = target;
      clozeMenu.style.left = x + 'px';
      clozeMenu.style.top = y + 'px';
      clozeMenu.style.display = 'flex';
    }
    function clozeWidthPx(value) {
      measureCtx.font = clozeFont;
      const text = value || '';
      const measured = Math.ceil(measureCtx.measureText(text).width);
      return Math.max(42, measured + 14);
    }
    function replaceNthClozeWithText(markdown, targetIndex) {
      let idx = 0;
      return (markdown || '').replace(/\\{\\{c::([\\s\\S]*?)\\}\\}/g, function(match, g1) {
        if (idx === targetIndex) {
          idx += 1;
          return g1;
        }
        idx += 1;
        return match;
      });
    }
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
      renderingConfig: { singleLineBreaks: true },
      previewRender: function(text) {
        let clozeIndex = 0;
        const replaced = text.replace(/\\{\\{c::([\\s\\S]*?)\\}\\}/g, function(_m, g1) {
          const widthPx = clozeWidthPx(g1);
          const idx = clozeIndex;
          clozeIndex += 1;
          return '<span class=\"cloze-box cloze-hidden\" data-answer=\"' + encodeURIComponent(g1) + '\" data-cloze-idx=\"' + idx + '\" data-width-px=\"' + widthPx + '\" style=\"width:' + widthPx + 'px\">▇▇▇</span>';
        });
        return marked.parse(replaced, { breaks: true, gfm: true });
      }
    });
    if (typeof editor.isPreviewActive === 'function' && !editor.isPreviewActive()) {
      editor.togglePreview();
    }
    document.addEventListener('contextmenu', function(ev) {
      const target = ev.target;
      if (!target || !target.classList || !target.classList.contains('cloze-box')) {
        hideClozeMenu();
        return;
      }
      ev.preventDefault();
      showClozeMenu(target, ev.pageX, ev.pageY);
    });
    document.addEventListener('click', function(ev) {
      if (clozeMenu.style.display === 'none') {
        return;
      }
      if (!clozeMenu.contains(ev.target)) {
        hideClozeMenu();
      }
    });
    document.addEventListener('click', function(ev) {
      const target = ev.target;
      if (!target || !target.classList) return;
      if (target.classList.contains('cloze-hidden')) {
        const answer = decodeURIComponent(target.getAttribute('data-answer') || '');
        const widthPx = target.getAttribute('data-width-px') || '42';
        target.style.width = widthPx + 'px';
        target.textContent = answer;
        target.classList.remove('cloze-hidden');
        target.classList.add('cloze-shown');
      } else if (target.classList.contains('cloze-shown')) {
        const widthPx = target.getAttribute('data-width-px') || '42';
        target.style.width = widthPx + 'px';
        target.textContent = '▇▇▇';
        target.classList.remove('cloze-shown');
        target.classList.add('cloze-hidden');
      }
    });
    clozeMenuToggle.addEventListener('click', function() {
      if (!clozeMenuTarget) return;
      if (clozeMenuTarget.classList.contains('cloze-hidden')) {
        clozeMenuTarget.click();
      } else if (clozeMenuTarget.classList.contains('cloze-shown')) {
        clozeMenuTarget.click();
      }
      hideClozeMenu();
    });
    clozeMenuUncloze.addEventListener('click', function() {
      if (!clozeMenuTarget) return;
      const idx = parseInt(clozeMenuTarget.getAttribute('data-cloze-idx') || '-1', 10);
      if (idx < 0) {
        hideClozeMenu();
        return;
      }
      const updated = replaceNthClozeWithText(editor.value(), idx);
      editor.value(updated);
      hideClozeMenu();
    });
    window.wrapSelectionCloze = function() {
      const cm = editor.codemirror;
      let selected = cm.getSelection();
      if (!selected || !selected.trim()) {
        const pos = cm.getCursor();
        const lineText = cm.getLine(pos.line) || '';
        let start = pos.ch;
        let end = pos.ch;
        while (start > 0 && /[^\\s]/.test(lineText[start - 1])) {
          start -= 1;
        }
        while (end < lineText.length && /[^\\s]/.test(lineText[end])) {
          end += 1;
        }
        selected = lineText.slice(start, end);
        if (!selected || !selected.trim()) return;
        cm.setSelection({ line: pos.line, ch: start }, { line: pos.line, ch: end });
      }
      if (selected.startsWith('{{c::') && selected.endsWith('}}')) {
        return;
      }
      cm.replaceSelection('{{c::' + selected + '}}');
    };
    window.setAllClozes = function(reveal) {
      const nodes = document.querySelectorAll('.cloze-hidden, .cloze-shown');
      for (const n of nodes) {
        const answer = decodeURIComponent(n.getAttribute('data-answer') || '');
        const widthPx = n.getAttribute('data-width-px') || '42';
        n.style.width = widthPx + 'px';
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
    window.setAllClozesReveal = function() { window.setAllClozes(true); };
    window.setAllClozesHide = function() { window.setAllClozes(false); };
    window.toggleAllClozes = function() {
      const anyHidden = document.querySelector('.cloze-hidden') !== null;
      window.setAllClozes(anyHidden);
    };
    window.getMarkdown = function() { return editor.value(); };
    window.setMarkdown = function(v) { editor.value(v || ''); };
  </script>
</body>
</html>
"""

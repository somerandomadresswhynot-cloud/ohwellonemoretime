import pytest

pytest.importorskip('PySide6')
pytest.importorskip('PySide6.QtWebEngineWidgets')

from PySide6.QtWidgets import QApplication
from study_app.ui.dialogs import (
    HintMarkdownDialog,
    RecallNoteDialog,
    _cloze_validation_messages,
    _extract_cloze_segments,
    _replace_nth_cloze,
)


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    return app


def test_extract_cloze_segments_keeps_markdown_and_extracts_values():
    text = '# Title\nUse {{c::memory}} in list\n- item'
    markdown_with_tokens, clozes = _extract_cloze_segments(text)
    assert clozes == ['memory']
    assert 'CLOZE_TOKEN_0' in markdown_with_tokens


def test_validation_reports_unclosed_and_nested():
    msgs = _cloze_validation_messages('a {{c::b {{c::c}} d')
    assert any('Unclosed cloze marker' in m for m in msgs)
    assert any('Nested cloze' in m for m in msgs)


def test_dialog_has_open_source_editor_controls(app):
    dlg = HintMarkdownDialog('alpha {{c::beta}}')
    try:
        assert dlg.make_cloze_btn.text()
        assert dlg.reveal_all_btn.text()
        assert dlg.hide_all_btn.text()
        assert dlg.toggle_all_btn.text()
        assert dlg.make_cloze_btn.isEnabled()
        assert dlg.reveal_all_btn.isEnabled()
        assert dlg.hide_all_btn.isEnabled()
        assert dlg.toggle_all_btn.isEnabled()
    finally:
        dlg.close()


def test_replace_nth_cloze_targets_exact_occurrence():
    text = 'A {{c::same}} B {{c::same}} C {{c::same}}'
    assert _replace_nth_cloze(text, 1) == 'A {{c::same}} B same C {{c::same}}'
    assert _replace_nth_cloze(text, 0) == 'A same B {{c::same}} C {{c::same}}'
    assert _replace_nth_cloze(text, 2) == 'A {{c::same}} B {{c::same}} C same'


def test_toolbar_clicks_dispatch_editor_actions(app):
    dlg = HintMarkdownDialog('alpha')
    seen = []
    try:
        dlg._run_editor_action = seen.append  # type: ignore[method-assign]
        dlg.make_cloze_btn.click()
        dlg.reveal_all_btn.click()
        dlg.hide_all_btn.click()
        dlg.toggle_all_btn.click()
        assert seen == [
            'window.wrapSelectionCloze',
            'window.setAllClozesReveal',
            'window.setAllClozesHide',
            'window.toggleAllClozes',
        ]
    finally:
        dlg.close()


def test_recall_note_dialog_emits_text_changed_signal(app):
    dlg = RecallNoteDialog('Pre', 'start')
    seen = []
    try:
        dlg.text_changed.connect(seen.append)
        dlg.editor.setPlainText('updated')
        dlg._emit_debounced_change()
        assert seen[-1] == 'updated'
    finally:
        dlg.close()

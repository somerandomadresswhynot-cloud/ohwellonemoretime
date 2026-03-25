import pytest

pytest.importorskip('PySide6')

from PySide6.QtWidgets import QApplication
from study_app.ui.dialogs import HintMarkdownDialog, _cloze_validation_messages, _extract_cloze_segments


@pytest.fixture(scope='module')
def app():
    app = QApplication.instance() or QApplication([])
    return app


def test_extract_cloze_segments_keeps_markdown_and_extracts_values():
    text = '# Title\nUse {{c::memory}} in list\n- item'
    markdown_with_tokens, clozes = _extract_cloze_segments(text)
    assert clozes == ['memory']
    assert 'CLOZE_TOKEN_0' in markdown_with_tokens
    assert '# Title' in markdown_with_tokens


def test_validation_reports_unclosed_and_nested():
    msgs = _cloze_validation_messages('a {{c::b {{c::c}} d')
    assert any('Unclosed cloze marker' in m for m in msgs)
    assert any('Nested cloze' in m for m in msgs)


def test_dialog_right_click_position_helpers_and_toggle_all(app):
    dlg = HintMarkdownDialog('alpha {{c::beta}} gamma')
    try:
        dlg._refresh_preview()
        assert len(dlg._cloze_values) == 1
        dlg._toggle_all_clozes()
        assert 0 in dlg._revealed_cloze_indexes
        dlg._toggle_all_clozes()
        assert 0 not in dlg._revealed_cloze_indexes
        dlg._toggle_mode()
        assert dlg.stack.currentWidget() is dlg.preview
        dlg._toggle_mode()
        assert dlg.stack.currentWidget() is dlg.editor

        text = dlg.editor.toPlainText()
        cloze_start = text.index('{{c::beta}}')
        cursor = dlg.editor.textCursor()
        cursor.setPosition(cloze_start + 3)
        dlg.editor.setTextCursor(cursor)
        bounds = dlg._cloze_bounds_at_cursor()
        assert bounds is not None

        dlg._remove_cloze(*bounds)
        assert '{{c::beta}}' not in dlg.editor.toPlainText()
        assert 'beta' in dlg.editor.toPlainText()
    finally:
        dlg.close()


def test_right_click_inside_selection_keeps_selection(app):
    dlg = HintMarkdownDialog('alpha beta gamma')
    try:
        text = dlg.editor.toPlainText()
        start = text.index('beta')
        end = start + len('beta')
        cursor = dlg.editor.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, cursor.KeepAnchor)
        dlg.editor.setTextCursor(cursor)
        pos = dlg.editor.cursorRect(cursor).center()
        dlg._apply_context_menu_cursor(pos)
        kept = dlg.editor.textCursor()
        assert kept.hasSelection()
        assert kept.selectionStart() == start
        assert kept.selectionEnd() == end
    finally:
        dlg.close()

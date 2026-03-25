import pytest

pytest.importorskip('PySide6')
pytest.importorskip('PySide6.QtWebEngineWidgets')

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
    finally:
        dlg.close()

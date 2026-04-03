from pathlib import Path


def test_hint_editor_uses_single_line_breaks_and_side_by_side_default():
    source = Path("study_app/ui/dialogs.py").read_text(encoding="utf-8")
    assert "renderingConfig: { singleLineBreaks: true }" in source
    assert "editor.toggleSideBySide();" in source

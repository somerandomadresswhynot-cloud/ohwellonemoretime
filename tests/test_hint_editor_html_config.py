from pathlib import Path


def test_hint_editor_uses_single_line_breaks_and_preview_default():
    source = Path("study_app/ui/dialogs.py").read_text(encoding="utf-8")
    assert "renderingConfig: { singleLineBreaks: true }" in source
    assert "marked.parse(replaced, { breaks: true, gfm: true })" in source
    assert "editor.togglePreview();" in source

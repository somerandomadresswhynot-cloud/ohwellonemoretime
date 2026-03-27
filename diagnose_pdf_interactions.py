from __future__ import annotations

"""
Minimal interactive harness for debugging PDF interaction behavior.

Usage:
  STUDY_APP_PDF_DEBUG=1 PYTHONPATH=. python diagnose_pdf_interactions.py /path/to/file.pdf
"""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from study_app.ui.pdf_viewer import PersistentPdfViewer


class ProbeWindow(QWidget):
    def __init__(self, pdf_path: str):
        super().__init__()
        self.setWindowTitle("PDF Interaction Probe")
        self.resize(1200, 900)
        self.viewer = PersistentPdfViewer()
        self.viewer.load_if_needed(pdf_path)
        self.viewer.set_fit_mode()
        self.viewer.set_annotation_tool("select_text")

        self.status = QLabel("Mode: select_text")
        self.selection = QLabel("Selection: <none>")
        self.selection.setWordWrap(True)

        mode_row = QHBoxLayout()
        for key, title in [
            ("select_text", "Select Text"),
            ("area", "Area"),
            ("erase", "Erase"),
            ("pan", "Pan"),
        ]:
            btn = QPushButton(title)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            mode_row.addWidget(btn)

        refresh_btn = QPushButton("Refresh selection snapshot")
        refresh_btn.clicked.connect(self._refresh_selection)

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addLayout(mode_row)
        layout.addWidget(refresh_btn)
        layout.addWidget(self.selection)
        layout.addWidget(self.viewer, 1)

        self.viewer.set_selection_menu_handler(self._on_selection_menu)
        self.viewer.set_area_created_handler(self._on_area_created)
        self.viewer.set_highlight_hit_handler(self._on_hit)

    def _set_mode(self, mode: str) -> None:
        self.viewer.set_annotation_tool(mode)
        self.status.setText(f"Mode: {mode}")

    def _refresh_selection(self) -> None:
        txt = self.viewer.selected_text()
        self.selection.setText(f"Selection: {txt[:200] if txt else '<none>'}")
        print(f"[pdf-probe] selected_text_len={len(txt)} page={self.viewer.view_state().get('page', 1)}")

    def _on_selection_menu(self, _pos, selected_text: str, page: int) -> None:
        print(f"[pdf-probe] context-menu page={page} selected_text_len={len((selected_text or '').strip())}")
        self.selection.setText(f"Selection: {(selected_text or '').strip()[:200] or '<none>'}")

    def _on_area_created(self, rect: dict, page: int) -> None:
        print(f"[pdf-probe] area-created page={page} rect={rect}")
        self.viewer.set_overlay_highlights([{"id": 1, "color": "#2d9cdb", "opacity": 0.35, "rects": [rect]}])

    def _on_hit(self, hid: int) -> None:
        print(f"[pdf-probe] erase-hit id={hid}")
        self.viewer.set_overlay_highlights([])


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python diagnose_pdf_interactions.py /path/to/file.pdf")
        return 2
    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.exists():
        print(f"file not found: {path}")
        return 2
    app = QApplication.instance() or QApplication(sys.argv)
    win = ProbeWindow(str(path))
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Interactive PDF viewer diagnostics for selection/copy/annotation.

Usage:
  python diagnose_pdf_interaction.py /path/to/file.pdf

This enables verbose viewer logging and opens PersistentPdfViewer directly so
mouse routing, context menus, selection, copy, and area-annotation creation
can be observed end-to-end in terminal output.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python diagnose_pdf_interaction.py /path/to/file.pdf")
        return 1
    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}")
        return 1

    os.environ["STUDY_APP_PDF_DEBUG"] = "1"
    try:
        from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
        from study_app.ui.pdf_viewer import PersistentPdfViewer
    except Exception as exc:
        print(f"PySide6/QtPdf unavailable: {exc}")
        return 2

    app = QApplication.instance() or QApplication([])
    root = QWidget()
    lay = QVBoxLayout(root)
    tip = QLabel(
        "Diagnostics active (STUDY_APP_PDF_DEBUG=1).\n"
        "Try: Select Text drag, Ctrl+C, right-click, Area drag, Erase click."
    )
    viewer = PersistentPdfViewer()
    viewer.set_selection_menu_handler(lambda pos, text, page: print(f"[diagnose] selection_menu page={page} text_len={len((text or '').strip())}"))
    viewer.set_area_created_handler(lambda rect, page: print(f"[diagnose] area_created page={page} rect={rect}"))
    viewer.set_highlight_hit_handler(lambda hid: print(f"[diagnose] highlight_hit id={hid}"))
    viewer.set_annotation_tool("select_text")
    viewer.load_if_needed(str(pdf_path))
    viewer.set_page(1)

    lay.addWidget(tip)
    lay.addWidget(viewer, 1)
    root.resize(1200, 900)
    root.setWindowTitle("PDF Interaction Diagnostics")
    root.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())

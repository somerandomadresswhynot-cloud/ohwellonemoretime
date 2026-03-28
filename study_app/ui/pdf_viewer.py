"""Compatibility module for the app's embedded PDF viewer.

The old QtPdf/overlay implementation has been removed.
Use `EmbeddedPdfViewer` (aliased as `PersistentPdfViewer`) backed by
`pdfjs-viewer-pyside6`.
"""

from study_app.ui.embedded_pdf_viewer import EmbeddedPdfViewer, PersistentPdfViewer

__all__ = ["EmbeddedPdfViewer", "PersistentPdfViewer"]

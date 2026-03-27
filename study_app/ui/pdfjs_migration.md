# PDF viewer migration (stock PDF.js generic viewer)

## Why old attempts were removed

The previous custom host (`pdfjs_host.html/js/css`) reimplemented viewer behavior, added bootstrap/network fallback logic, and created integration complexity.
This migration switches to the official PDF.js generic viewer and keeps app code as a thin adapter.

## New architecture

- `study_app/ui/pdf_stock_viewer.py`
  - PySide6 `QWebEngineView` wrapper.
  - Loads vendored `web/viewer.html`.
  - Sets up `QWebChannel` and forwards events.
- `study_app/ui/web/pdfjs_bridge.js`
  - Thin stock-viewer bridge (`window.ohwPdfHost`) for Python->JS operations.
- `study_app/ui/web/pdfjs_annotations.js`
  - App-owned page-space area annotations + erase mode.
- `study_app/ui/web/pdfjs_bridge.css`
  - Minimal annotation-layer styling.

## Required vendored assets

Place official PDF.js generic build assets under package tree (no runtime downloads):

- `study_app/ui/web/vendor/pdfjs/web/viewer.html`
- `study_app/ui/web/vendor/pdfjs/web/viewer.js`
- `study_app/ui/web/vendor/pdfjs/build/pdf.mjs`
- `study_app/ui/web/vendor/pdfjs/build/pdf.worker.mjs`
- plus other upstream files referenced by `viewer.html` (`viewer.css`, locale/cmaps/images, etc.)

## Manual verification checklist

1. Launch app.
2. Open source workspace.
3. Open study queue unit.
4. Verify PDF opens in stock viewer UI.
5. Drag-select visible text and copy.
6. Paste selection into pre/post note.
7. Switch to Area mode and draw annotation.
8. Zoom and scroll; annotation stays aligned.
9. Switch to Erase mode and delete annotation.
10. Reopen same source/unit and confirm annotations persist.

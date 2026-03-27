# PDF Viewer Migration (QtPdf ➜ PDF.js + QWebEngine)

## Why the old viewer was removed
The prior `QtPdf/QPdfView` path could not reliably deliver native drag text selection + copy and stable annotation placement. The new viewer uses browser-native PDF.js text layers and a page-space annotation model to address those issues from first principles.

## New subsystem
- `study_app/ui/pdfjs_viewer.py` (PySide shell + `QWebEngineView` + `QWebChannel` bridge)
- `study_app/ui/web/pdfjs_host.html`
- `study_app/ui/web/pdfjs_host.js`
- `study_app/ui/web/pdfjs_host.css`
- `third_party/pdfjs/` (vendored dependency location)

## Bridge API
Python -> JS:
- `open_pdf(file_path, initial_page=None)`
- `go_to_page(page_number)`
- `set_zoom(mode_or_value)`
- `set_tool(tool)`
- `load_annotations(annotation_payload)`
- `request_selected_text()`
- `copy_selected_text()`
- `clear_selection()`

JS -> Python:
- `selection_changed(selected_text, page_info)`
- `annotation_created(annotation_payload)`
- `annotation_deleted(annotation_id)`
- `page_changed(page_number)`
- `viewer_ready(capabilities)`

## Manual verification checklist
1. Open a text-based PDF source.
2. Confirm default tool is **Select Text**.
3. Drag-select text directly in viewer and copy (Ctrl/Cmd+C).
4. Paste into pre-recall note.
5. Switch to **Area**, drag rectangle annotation.
6. Zoom in/out and scroll; annotation remains aligned.
7. Switch to **Erase**, click annotation; it is deleted.
8. Add post-recall note and paste copied text.
9. Reopen same source/unit; annotation persists and remains aligned.

## Dependency note
This migration expects PDF.js assets under `third_party/pdfjs/build/` (not fetched at runtime).

# Study PDF Desktop MVP

Local Python desktop app (PySide6 + SQLite) for PDF reading and scheduled rereading/review.

Requires **Python 3.10+** (3.11+ recommended).

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
study-app
```

Database is stored at `./study_app.db` by default.

## Windows quick start

Use the reusable launcher (it now validates Python version (3.10+) before install):

```bat
run_study_app.bat
```

Optional commands:

```bat
run_study_app.bat setup
run_study_app.bat update
run_study_app.bat resetdb
```

When double-clicked from Explorer (`run_study_app.bat` with no args), the launcher now pauses at the end so logs stay visible.

Optional behavior:

```bat
set KEEP_OPEN_ON_ERROR=1
run_study_app.bat run

set KEEP_OPEN_ALWAYS=1
run_study_app.bat setup
```

## Core flow

1. Import a PDF from **Sources**.
2. Open **Workspace** for a source.
3. Edit outline text (`# Heading [p1-3]`) and apply.
4. Use **Study Queue** for due-only review with timer/notes/ratings.
5. Open review history to edit notes or delete mistaken events.
6. Restart app; data persists in SQLite.

## Embedded PDF viewer migration (pdfjs-viewer-pyside6)

The app now uses `pdfjs-viewer-pyside6` as the single embedded PDF viewer path.

### What changed

- The previous QtPdf (`QPdfView`) + custom overlay/annotation rendering stack was removed from the viewer module.
- `study_app/ui/embedded_pdf_viewer.py` now wraps `pdfjs_viewer.PDFViewerWidget` with a minimal app-facing API.
- `study_app/ui/pdf_viewer.py` is now only a compatibility re-export of the new wrapper.
- Dependency constraints now align with the new widget (`PySide6>=6.10.0,!=6.10.1`, `pdfjs-viewer-pyside6>=1.1.2`).

### Scope in this replacement

- Supported and prioritized:
  - open local PDFs
  - page scrolling
  - viewer-native zoom controls
  - text selection + copy/paste behavior through the embedded PDF.js viewer
- Legacy overlay-specific methods are intentionally no-ops in the wrapper so older call sites do not re-enable the removed overlay path.

### Manual verification checklist

1. Launch app (`study-app`).
2. Open a source workspace and a known text PDF.
3. Select text with mouse drag, copy with `Ctrl+C`, and paste into recall note fields.
4. Confirm scrolling and zoom work in the embedded viewer.
5. Open Study Queue and verify the same PDF viewer behavior there.
6. Confirm there is no old overlay shell/UI rendering over the PDF content.

## FSRS scheduler notes

- Scheduling now uses an FSRS-style DSR model (`difficulty`, `stability`, `retrievability`) in `study_app/services/fsrs_scheduler.py`.
- Existing review history is replayed on demand to bootstrap FSRS state for old units that do not yet have FSRS fields populated.
- App feedback mapping:
  - `skip` -> Again (fail)
  - `hard` -> Hard (successful but difficult)
  - `with_effort` -> Good
  - `easy` -> Easy
- The raw interval is computed from desired retention (default `0.90`), then a policy layer clamps successful recalls to at least the next day for coarse-grained chapter/section review.
- Due timestamps are finally rounded to local day start for storage consistency.
- Parameter optimization is intentionally not included yet; the insertion point is `DEFAULT_FSRS_PARAMETERS` / injected `FSRSParameters` in `fsrs_scheduler.py`.

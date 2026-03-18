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

## PDF annotation modes (workspace)

Inside a source workspace, the PDF panel supports annotation tooling:

- **Select Text**: select text and right-click to add/remove text highlights.
- **Area**: drag on the page to create a rectangular area highlight.
- **Pan**: switch cursor for navigation-focused reading.
- **Erase**: click an existing overlay highlight to remove it.

Annotation toolbar state is persisted in `ui_state`:

- `pdf_annotation_tool`
- `pdf_annotation_color`
- `pdf_annotation_opacity`
- A lightweight **text-layer probe hint** (`Text layer: likely yes/no`) appears in annotation controls to help diagnose why text selection may not snap on some PDFs.

### Known limitations / fallback behavior

- `QPdfView` does not reliably expose text glyph quad geometry across Qt versions, so text highlight rendering uses a **fallback cue overlay** on the current page rather than exact text-shape painting.
- Area highlight rectangles are currently normalized to the visible viewport and intended as a foundation for richer page-geometry anchoring in future updates.
- UI interactions (drag, erase, context menu) are interactive behaviors; automated UI smoke coverage is best-effort and skipped when GUI dependencies are unavailable.

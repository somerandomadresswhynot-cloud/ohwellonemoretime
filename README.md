# Study PDF Desktop MVP

Local Python desktop app (PySide6 + SQLite) for PDF reading and scheduled rereading/review.

Requires **Python 3.11+**.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
study-app
```

Database is stored at `./study_app.db` by default.

## Windows quick start

Use the reusable launcher (it now validates Python version before install):

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

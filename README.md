# Study PDF Desktop MVP

Local Python desktop app (PySide6 + SQLite) for PDF reading and scheduled rereading/review.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
study-app
```

Database is stored at `./study_app.db` by default.

## Core flow

1. Import a PDF from **Sources**.
2. Open **Workspace** for a source.
3. Edit outline text (`# Heading [p1-3]`) and apply.
4. Use **Study Queue** for due-only review with timer/notes/ratings.
5. Open review history to edit notes or delete mistaken events.
6. Restart app; data persists in SQLite.

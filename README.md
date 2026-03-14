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

## Windows quick start

Use the reusable launcher:

```bat
run_study_app.bat
```

Optional commands:

```bat
run_study_app.bat setup
run_study_app.bat update
run_study_app.bat resetdb
```

If launch errors close too quickly from Explorer, run from `cmd.exe`, or set:

```bat
set KEEP_OPEN_ON_ERROR=1
run_study_app.bat
```

## Core flow

1. Import a PDF from **Sources**.
2. Open **Workspace** for a source.
3. Edit outline text (`# Heading [p1-3]`) and apply.
4. Use **Study Queue** for due-only review with timer/notes/ratings.
5. Open review history to edit notes or delete mistaken events.
6. Restart app; data persists in SQLite.

from datetime import timedelta
import tempfile

from study_app.persistence.database import Database
from study_app.persistence.repositories import (
    EVENT_AGAIN,
    EVENT_LEARNING_SUBMIT,
    EVENT_TO_STABILIZING,
    EVENT_FSRS_REVIEW,
    OutlineRepo,
    ReviewRepo,
    SourceRepo,
    derive_unit_mode_and_due,
)


def _seed_unit(db: Database):
    source_repo = SourceRepo(db)
    outline_repo = OutlineRepo(db)
    review_repo = ReviewRepo(db)
    source_id = source_repo.create("Book", "/tmp/book.pdf", 1, 50)
    outline_repo.replace_outline(source_id, [
        {"depth": 1, "title": "Book", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
        {"depth": 2, "title": "U1", "start_page": 1, "end_page": 3, "is_unit": True, "queue_enabled": True},
    ])
    unit = review_repo.source_units(source_id)[0]
    return review_repo, int(unit["id"])


def _payload(at: str, rating: str = "with_effort"):
    return {
        "started_at": at,
        "ended_at": at,
        "elapsed_seconds": 60,
        "rating": rating,
        "pre_note": "",
        "post_note": "",
    }


def _stats(at: str, count: int):
    return {
        "last_review_at": at,
        "review_count": count,
        "ease_factor": 2.5,
        "avg_rating": 3.0,
        "fsrs_difficulty": 5.0,
        "fsrs_stability": 3.0,
        "fsrs_last_review_at": at,
        "fsrs_last_grade": 3,
        "fsrs_review_count": count,
        "fsrs_lapse_count": 0,
        "fsrs_state_version": 1,
        "fsrs_due_retention_used": 0.9,
    }


def test_learning_submit_is_not_active_fsrs_cycle():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo, unit_id = _seed_unit(db)
            repo.record_learning_event(unit_id, _payload("2026-03-01T10:00:00+00:00"), EVENT_LEARNING_SUBMIT)
            events = repo.review_history_for_units([unit_id])[unit_id]
            derived = derive_unit_mode_and_due(events)
            assert derived["mode"] == "learning"
            assert derived["active_fsrs_events"] == []
            assert derived["due_at"] is not None
        finally:
            db.close()


def test_to_stabilizing_sets_reset_and_next_day_due():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo, unit_id = _seed_unit(db)
            repo.record_learning_event(unit_id, _payload("2026-03-02T09:00:00+00:00"), EVENT_TO_STABILIZING)
            events = repo.review_history_for_units([unit_id])[unit_id]
            derived = derive_unit_mode_and_due(events)
            assert derived["mode"] == "stabilizing"
            assert derived["active_fsrs_events"] == []
            assert derived["due_at"] is not None
        finally:
            db.close()


def test_again_resets_active_cycle_and_old_cycle_ignored():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo, unit_id = _seed_unit(db)
            repo.record_review(unit_id, _payload("2026-03-01T10:00:00+00:00", "hard"), _stats("2026-03-01T10:00:00+00:00", 1))
            repo.record_review(unit_id, _payload("2026-03-05T10:00:00+00:00", "easy"), _stats("2026-03-05T10:00:00+00:00", 2))
            repo.record_learning_event(unit_id, _payload("2026-03-06T10:00:00+00:00"), EVENT_AGAIN)

            events = repo.review_history_for_units([unit_id])[unit_id]
            derived = derive_unit_mode_and_due(events)
            assert derived["mode"] == "stabilizing"
            assert derived["active_fsrs_events"] == []

            # New FSRS review after reset starts fresh active cycle.
            repo.record_review(unit_id, _payload("2026-03-07T10:00:00+00:00", "with_effort"), _stats("2026-03-07T10:00:00+00:00", 1))
            events2 = repo.review_history_for_units([unit_id])[unit_id]
            derived2 = derive_unit_mode_and_due(events2)
            assert derived2["mode"] == "review"
            assert len(derived2["active_fsrs_events"]) == 1
            assert derived2["active_fsrs_events"][0]["ended_at"] == "2026-03-07T10:00:00+00:00"
        finally:
            db.close()


def test_due_units_include_learning_and_stabilizing_next_day():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo, unit_id = _seed_unit(db)
            repo.record_learning_event(unit_id, _payload("2026-03-01T09:00:00+00:00"), EVENT_LEARNING_SUBMIT)
            due = {int(u.unit_id) for u in repo.due_units("2026-03-03T12:00:00+00:00")}
            assert unit_id in due
            repo.record_learning_event(unit_id, _payload("2026-03-03T12:00:00+00:00"), EVENT_TO_STABILIZING)
            due_after = {int(u.unit_id) for u in repo.due_units("2026-03-05T12:00:00+00:00")}
            assert unit_id in due_after
        finally:
            db.close()


def test_migration_backfills_event_kind_and_fsrs_grade_from_legacy_skip():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        import sqlite3

        conn = sqlite3.connect(tmp.name)
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                page_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE outline_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                parent_id INTEGER,
                title TEXT NOT NULL,
                depth INTEGER NOT NULL,
                order_index INTEGER NOT NULL,
                start_page INTEGER,
                end_page INTEGER,
                is_unit INTEGER NOT NULL DEFAULT 0,
                queue_enabled INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
            );
            CREATE TABLE units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                node_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                start_page INTEGER NOT NULL,
                end_page INTEGER NOT NULL,
                queue_enabled INTEGER NOT NULL DEFAULT 1,
                last_review_at TEXT,
                next_review_at TEXT,
                review_count INTEGER NOT NULL DEFAULT 0,
                ease_factor REAL NOT NULL DEFAULT 2.5,
                interval_days REAL NOT NULL DEFAULT 0,
                avg_rating REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                FOREIGN KEY(node_id) REFERENCES outline_nodes(id) ON DELETE CASCADE
            );
            CREATE TABLE review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_id INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                elapsed_seconds INTEGER NOT NULL,
                rating TEXT NOT NULL,
                pre_note TEXT NOT NULL DEFAULT '',
                post_note TEXT NOT NULL DEFAULT '',
                interval_days REAL NOT NULL,
                next_review_at TEXT NOT NULL,
                deleted_at TEXT,
                FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
            );
            CREATE TABLE highlights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                unit_id INTEGER,
                page INTEGER NOT NULL,
                quote_text TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL
            );
            CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        conn.execute(
            "INSERT INTO sources(title,file_path,file_size,page_count,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("Book", "/tmp/book.pdf", 1, 10, 1, "2026-03-01T00:00:00+00:00", "2026-03-01T00:00:00+00:00"),
        )
        source_id = int(conn.execute("SELECT id FROM sources").fetchone()[0])
        conn.execute(
            "INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
            (source_id, None, "U1", 2, 1, 1, 3, 1, 1),
        )
        node_id = int(conn.execute("SELECT id FROM outline_nodes").fetchone()[0])
        conn.execute(
            "INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, node_id, "U1", 1, 3, 1, None, None, 0, 2.5, 0.0, 0.0),
        )
        unit_id = int(conn.execute("SELECT id FROM units").fetchone()[0])
        conn.execute(
            "INSERT INTO review_events(unit_id,started_at,ended_at,elapsed_seconds,rating,pre_note,post_note,interval_days,next_review_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (unit_id, "2026-03-01T10:00:00+00:00", "2026-03-01T10:00:00+00:00", 60, "skip", "", "", 0.0, "2026-03-01T10:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        db = Database(tmp.name)
        try:
            row = db.conn.execute("SELECT event_kind,fsrs_grade FROM review_events WHERE unit_id=?", (unit_id,)).fetchone()
            assert row["event_kind"] == "again"
            assert row["fsrs_grade"] is None
        finally:
            db.close()

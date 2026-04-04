import sqlite3
import tempfile
import unittest

from study_app.persistence.database import Database


class DatabaseConstraintTests(unittest.TestCase):
    def _seed_minimal_source_unit(self, db: Database) -> tuple[int, int]:
        db.conn.execute(
            """INSERT INTO sources(title,file_path,file_size,page_count,is_active,learning_mode,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            ("Book", "/tmp/book.pdf", 1, 100, 1, "any", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        source_id = int(db.conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"])
        db.conn.execute(
            """INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (source_id, None, "Unit 1", 2, 1, 10, 12, 1, 1),
        )
        node_id = int(db.conn.execute("SELECT id FROM outline_nodes LIMIT 1").fetchone()["id"])
        db.conn.execute(
            """INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source_id, node_id, "Unit 1", 10, 12, 1, None, None, 0, 2.5, 0.0, 0.0),
        )
        unit_id = int(db.conn.execute("SELECT id FROM units LIMIT 1").fetchone()["id"])
        db.conn.commit()
        return source_id, unit_id

    def test_new_database_enforces_constraints(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            source_id, unit_id = self._seed_minimal_source_unit(db)

            with self.assertRaises(sqlite3.IntegrityError):
                db.conn.execute(
                    """INSERT INTO review_events(
                        unit_id, started_at, ended_at, elapsed_seconds, rating, event_kind, fsrs_grade, pre_note, post_note, interval_days, next_review_at, deleted_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        unit_id,
                        "2026-01-01T10:00:00+00:00",
                        "2026-01-01T10:03:00+00:00",
                        -5,
                        "easy",
                        "fsrs_review",
                        "easy",
                        "",
                        "",
                        1.0,
                        "2026-01-02T10:03:00+00:00",
                        None,
                    ),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                db.conn.execute(
                    """INSERT INTO review_events(
                        unit_id, started_at, ended_at, elapsed_seconds, rating, event_kind, fsrs_grade, pre_note, post_note, interval_days, next_review_at, deleted_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        unit_id,
                        "2026-01-01T10:00:00+00:00",
                        "2026-01-01T10:03:00+00:00",
                        180,
                        "invalid",
                        "fsrs_review",
                        "invalid",
                        "",
                        "",
                        1.0,
                        "2026-01-02T10:03:00+00:00",
                        None,
                    ),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                db.conn.execute(
                    """INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (source_id, None, "Bad range", 1, 2, 30, 20, 0, 1),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                db.conn.execute(
                    """INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (source_id, None, "Unit 2", 2, 3, 15, 16, 1, 1),
                )
                node_2 = int(
                    db.conn.execute("SELECT id FROM outline_nodes WHERE title='Unit 2'").fetchone()["id"]
                )
                db.conn.execute(
                    """INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source_id, node_2, "Bad unit", 20, 10, 1, None, None, 0, 2.5, 0.0, 0.0),
                )

    def test_existing_database_is_migrated_to_enforce_constraints(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
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
                    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_id) REFERENCES outline_nodes(id) ON DELETE CASCADE
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
            conn.close()

            upgraded = Database(tmp.name)
            self.addCleanup(upgraded.close)
            upgraded.conn.execute(
                """INSERT INTO sources(title,file_path,file_size,page_count,is_active,learning_mode,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("Book", "/tmp/book.pdf", 1, 100, 1, "any", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )
            source_id = int(upgraded.conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"])
            upgraded.conn.execute(
                """INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (source_id, None, "Unit 1", 2, 1, 10, 12, 1, 1),
            )
            node_id = int(upgraded.conn.execute("SELECT id FROM outline_nodes LIMIT 1").fetchone()["id"])
            upgraded.conn.execute(
                """INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, node_id, "Unit 1", 10, 12, 1, None, None, 0, 2.5, 0.0, 0.0),
            )
            unit_id = int(upgraded.conn.execute("SELECT id FROM units LIMIT 1").fetchone()["id"])

            with self.assertRaises(sqlite3.IntegrityError):
                upgraded.conn.execute(
                    """INSERT INTO review_events(
                        unit_id, started_at, ended_at, elapsed_seconds, rating, event_kind, fsrs_grade, pre_note, post_note, interval_days, next_review_at, deleted_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        unit_id,
                        "2026-01-01T10:00:00+00:00",
                        "2026-01-01T10:03:00+00:00",
                        -1,
                        "easy",
                        "fsrs_review",
                        "easy",
                        "",
                        "",
                        1.0,
                        "2026-01-02T10:03:00+00:00",
                        None,
                    ),
                )


if __name__ == "__main__":
    unittest.main()

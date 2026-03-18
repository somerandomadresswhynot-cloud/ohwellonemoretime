from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    learning_mode TEXT NOT NULL DEFAULT 'any',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outline_nodes (
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
    CHECK (start_page IS NULL OR start_page >= 1),
    CHECK (end_page IS NULL OR (start_page IS NOT NULL AND end_page >= start_page)),
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY(parent_id) REFERENCES outline_nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS units (
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
    CHECK (start_page >= 1),
    CHECK (end_page >= start_page),
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY(node_id) REFERENCES outline_nodes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_events (
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
    CHECK (elapsed_seconds >= 0),
    CHECK (rating IN ('easy','with_effort','hard','skip')),
    FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_event_id INTEGER NOT NULL,
    changed_at TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    FOREIGN KEY(review_event_id) REFERENCES review_events(id) ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    unit_id INTEGER,
    page INTEGER NOT NULL,
    page_index INTEGER NOT NULL DEFAULT 0,
    quote_text TEXT NOT NULL,
    anchor_type TEXT NOT NULL DEFAULT 'text',
    text_prefix TEXT NOT NULL DEFAULT '',
    text_exact TEXT NOT NULL DEFAULT '',
    text_suffix TEXT NOT NULL DEFAULT '',
    rects_json TEXT NOT NULL DEFAULT '[]',
    opacity REAL NOT NULL DEFAULT 0.35,
    label TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#2d9cdb',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    CHECK (page >= 1),
    CHECK (page_index >= 0),
    CHECK (anchor_type IN ('text','rect')),
    CHECK (opacity >= 0.0 AND opacity <= 1.0),
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
    FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ui_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_outline_nodes_source_order
ON outline_nodes(source_id, order_index);

CREATE INDEX IF NOT EXISTS idx_units_due_queue
ON units(next_review_at, queue_enabled, source_id);

CREATE INDEX IF NOT EXISTS idx_units_source_queue
ON units(source_id, queue_enabled);

CREATE INDEX IF NOT EXISTS idx_review_events_unit_deleted_ended
ON review_events(unit_id, deleted_at, ended_at DESC);

CREATE INDEX IF NOT EXISTS idx_review_events_unit_deleted
ON review_events(unit_id, deleted_at);
"""


class Database:
    def __init__(self, path: str = "study_app.db") -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.executescript(INDEXES)
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        source_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(sources)").fetchall()}
        if "learning_mode" not in source_cols:
            self.conn.execute("ALTER TABLE sources ADD COLUMN learning_mode TEXT NOT NULL DEFAULT 'any'")

        highlight_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(highlights)").fetchall()}
        if "color" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN color TEXT NOT NULL DEFAULT '#2d9cdb'")
        if "page_index" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN page_index INTEGER NOT NULL DEFAULT 0")
        if "anchor_type" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN anchor_type TEXT NOT NULL DEFAULT 'text'")
        if "text_prefix" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN text_prefix TEXT NOT NULL DEFAULT ''")
        if "text_exact" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN text_exact TEXT NOT NULL DEFAULT ''")
        if "text_suffix" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN text_suffix TEXT NOT NULL DEFAULT ''")
        if "rects_json" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN rects_json TEXT NOT NULL DEFAULT '[]'")
        if "opacity" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN opacity REAL NOT NULL DEFAULT 0.35")
        if "label" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN label TEXT NOT NULL DEFAULT ''")
        if "updated_at" not in highlight_cols:
            self.conn.execute("ALTER TABLE highlights ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        self._backfill_highlight_annotation_fields()
        self.conn.commit()
        self._ensure_scheduling_constraints()
        self._ensure_indexes()

    def _backfill_highlight_annotation_fields(self) -> None:
        self.conn.execute(
            "UPDATE highlights SET page_index = CASE WHEN page >= 1 THEN page - 1 ELSE 0 END WHERE page_index < 0 OR page_index IS NULL"
        )
        self.conn.execute(
            "UPDATE highlights SET page_index = page - 1 WHERE page_index = 0 AND page > 1"
        )
        self.conn.execute(
            "UPDATE highlights SET text_exact = quote_text WHERE trim(text_exact) = ''"
        )
        self.conn.execute(
            "UPDATE highlights SET anchor_type = 'text' WHERE trim(anchor_type) = ''"
        )
        self.conn.execute(
            "UPDATE highlights SET anchor_type = 'text' WHERE anchor_type NOT IN ('text','rect')"
        )
        self.conn.execute(
            "UPDATE highlights SET rects_json = '[]' WHERE trim(rects_json) = ''"
        )
        self.conn.execute(
            "UPDATE highlights SET opacity = CASE WHEN opacity < 0 THEN 0 WHEN opacity > 1 THEN 1 ELSE opacity END"
        )
        self.conn.execute(
            "UPDATE highlights SET updated_at = created_at WHERE trim(updated_at) = ''"
        )

    def _ensure_indexes(self) -> None:
        self.conn.executescript(INDEXES)

    def _table_sql(self, table_name: str) -> str:
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return str(row["sql"] or "") if row else ""

    def _table_has_constraint_markers(self, table_name: str, markers: list[str]) -> bool:
        sql = self._table_sql(table_name).lower()
        return all(marker in sql for marker in markers)

    def _ensure_scheduling_constraints(self) -> None:
        outline_ok = self._table_has_constraint_markers(
            "outline_nodes",
            ["check (start_page is null or start_page >= 1)", "check (end_page is null or (start_page is not null and end_page >= start_page))"],
        )
        units_ok = self._table_has_constraint_markers(
            "units",
            ["check (start_page >= 1)", "check (end_page >= start_page)"],
        )
        review_ok = self._table_has_constraint_markers(
            "review_events",
            ["check (elapsed_seconds >= 0)", "check (rating in ('easy','with_effort','hard','skip'))"],
        )
        if outline_ok and units_ok and review_ok:
            return

        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.execute("BEGIN")
            if not outline_ok:
                self._rebuild_outline_nodes_with_constraints()
            if not units_ok:
                self._rebuild_units_with_constraints()
            if not review_ok:
                self._rebuild_review_events_with_constraints()
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")
        violations = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError("Foreign key violations detected after constraint migration")

    def _rebuild_outline_nodes_with_constraints(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE outline_nodes_new (
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
                CHECK (start_page IS NULL OR start_page >= 1),
                CHECK (end_page IS NULL OR (start_page IS NOT NULL AND end_page >= start_page)),
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                FOREIGN KEY(parent_id) REFERENCES outline_nodes_new(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO outline_nodes_new (
                id, source_id, parent_id, title, depth, order_index, start_page, end_page, is_unit, queue_enabled
            )
            SELECT id, source_id, parent_id, title, depth, order_index, start_page, end_page, is_unit, queue_enabled
            FROM outline_nodes
            """
        )
        self.conn.execute("DROP TABLE outline_nodes")
        self.conn.execute("ALTER TABLE outline_nodes_new RENAME TO outline_nodes")

    def _rebuild_units_with_constraints(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE units_new (
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
                CHECK (start_page >= 1),
                CHECK (end_page >= start_page),
                FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE,
                FOREIGN KEY(node_id) REFERENCES outline_nodes(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO units_new (
                id, source_id, node_id, title, start_page, end_page, queue_enabled,
                last_review_at, next_review_at, review_count, ease_factor, interval_days, avg_rating
            )
            SELECT
                id, source_id, node_id, title, start_page, end_page, queue_enabled,
                last_review_at, next_review_at, review_count, ease_factor, interval_days, avg_rating
            FROM units
            """
        )
        self.conn.execute("DROP TABLE units")
        self.conn.execute("ALTER TABLE units_new RENAME TO units")

    def _rebuild_review_events_with_constraints(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE review_events_new (
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
                CHECK (elapsed_seconds >= 0),
                CHECK (rating IN ('easy','with_effort','hard','skip')),
                FOREIGN KEY(unit_id) REFERENCES units(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.execute(
            """
            INSERT INTO review_events_new (
                id, unit_id, started_at, ended_at, elapsed_seconds, rating, pre_note, post_note, interval_days, next_review_at, deleted_at
            )
            SELECT
                id, unit_id, started_at, ended_at, elapsed_seconds, rating, pre_note, post_note, interval_days, next_review_at, deleted_at
            FROM review_events
            """
        )
        self.conn.execute("DROP TABLE review_events")
        self.conn.execute("ALTER TABLE review_events_new RENAME TO review_events")

    def close(self) -> None:
        self.conn.close()

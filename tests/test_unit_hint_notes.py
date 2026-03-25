import sqlite3
import tempfile

from study_app.persistence.database import Database
from study_app.persistence.repositories import ReviewRepo


def _seed_unit(db: Database) -> int:
    now = '2026-03-25T00:00:00+00:00'
    db.conn.execute(
        "INSERT INTO sources(title,file_path,file_size,page_count,is_active,learning_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ('Book', '/tmp/book.pdf', 0, 12, 1, 'any', now, now),
    )
    source_id = int(db.conn.execute('SELECT id FROM sources').fetchone()['id'])
    db.conn.execute(
        "INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
        (source_id, None, 'U1', 1, 0, 1, 2, 1, 1),
    )
    node_id = int(db.conn.execute('SELECT id FROM outline_nodes').fetchone()['id'])
    db.conn.execute(
        "INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, node_id, 'U1', 1, 2, 1, None, None, 0, 2.5, 0.0, 0.0),
    )
    db.conn.commit()
    return int(db.conn.execute('SELECT id FROM units').fetchone()['id'])


def test_units_have_hint_markdown_column():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            cols = {r['name'] for r in db.conn.execute('PRAGMA table_info(units)').fetchall()}
            assert 'hint_markdown' in cols
        finally:
            db.close()


def test_save_unit_hint_records_revision():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo = ReviewRepo(db)
            unit_id = _seed_unit(db)
            changed = repo.save_unit_hint_markdown(unit_id, 'alpha {{c::beta}} gamma')
            assert changed is True
            assert repo.unit_hint_markdown(unit_id) == 'alpha {{c::beta}} gamma'
            revisions = repo.unit_hint_revisions(unit_id)
            assert len(revisions) == 1
            assert revisions[0]['before_markdown'] == ''
            assert revisions[0]['after_markdown'] == 'alpha {{c::beta}} gamma'
            assert repo.unit_hint_last_changed_at(unit_id) == revisions[0]['changed_at']
        finally:
            db.close()


def test_save_unit_hint_noop_does_not_add_revision():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo = ReviewRepo(db)
            unit_id = _seed_unit(db)
            assert repo.save_unit_hint_markdown(unit_id, 'same text') is True
            assert repo.save_unit_hint_markdown(unit_id, 'same text') is False
            revisions = repo.unit_hint_revisions(unit_id)
            assert len(revisions) == 1
        finally:
            db.close()


def test_old_database_gets_hint_column_and_revisions_table():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
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
                queue_enabled INTEGER NOT NULL DEFAULT 1
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
                avg_rating REAL NOT NULL DEFAULT 0
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
                    deleted_at TEXT
                );
            CREATE TABLE highlights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                unit_id INTEGER,
                page INTEGER NOT NULL,
                quote_text TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        conn.close()
        db = Database(tmp.name)
        try:
            unit_cols = {r['name'] for r in db.conn.execute('PRAGMA table_info(units)').fetchall()}
            assert 'hint_markdown' in unit_cols
            tables = {r['name'] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            assert 'unit_hint_revisions' in tables
        finally:
            db.close()


def test_revision_pagination_filter_and_cap():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            repo = ReviewRepo(db)
            unit_id = _seed_unit(db)
            for i in range(520):
                repo.save_unit_hint_markdown(unit_id, f'ver-{i}')
            rows = repo.unit_hint_revisions(unit_id, limit=1000)
            assert len(rows) == 500
            newest = rows[0]['id']
            older = repo.unit_hint_revisions(unit_id, limit=10, before_revision_id=newest)
            assert older
            assert all(r['id'] < newest for r in older)
            filtered = repo.unit_hint_revisions(unit_id, limit=20, search_text='ver-51')
            assert filtered
            assert any('ver-51' in r['after_markdown'] for r in filtered)
        finally:
            db.close()

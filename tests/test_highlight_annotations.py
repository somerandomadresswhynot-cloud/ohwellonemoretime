import json
import sqlite3
import tempfile
import unittest

from study_app.persistence.database import Database
from study_app.persistence.repositories import HighlightRepo, SourceRepo


class HighlightAnnotationsTests(unittest.TestCase):
    def test_existing_highlights_are_backfilled_for_annotation_fields(self):
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
                CREATE TABLE highlights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    unit_id INTEGER,
                    page INTEGER NOT NULL,
                    quote_text TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT INTO sources(title,file_path,file_size,page_count,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ('Book', '/tmp/book.pdf', 10, 100, 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            )
            source_id = int(conn.execute('SELECT id FROM sources LIMIT 1').fetchone()[0])
            conn.execute(
                "INSERT INTO highlights(source_id,unit_id,page,quote_text,note,created_at) VALUES(?,?,?,?,?,?)",
                (source_id, None, 7, 'A quote', 'note', '2026-01-01T00:00:00+00:00'),
            )
            conn.commit()
            conn.close()

            db = Database(tmp.name)
            self.addCleanup(db.close)
            row = db.conn.execute('SELECT * FROM highlights LIMIT 1').fetchone()
            self.assertEqual(row['page_index'], 6)
            self.assertEqual(row['anchor_type'], 'text')
            self.assertEqual(row['text_exact'], 'A quote')
            self.assertEqual(row['rects_json'], '[]')
            self.assertEqual(row['updated_at'], '2026-01-01T00:00:00+00:00')

    def test_backfill_normalizes_invalid_anchor_type_and_opacity(self):
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
                    learning_mode TEXT NOT NULL DEFAULT 'any',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE highlights (
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
                    rects_json TEXT NOT NULL DEFAULT '',
                    opacity REAL NOT NULL DEFAULT 0.35,
                    label TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '#2d9cdb',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                """
            )
            conn.execute(
                "INSERT INTO sources(title,file_path,file_size,page_count,is_active,learning_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ('Book', '/tmp/book.pdf', 1, 100, 1, 'any', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'),
            )
            source_id = int(conn.execute("SELECT id FROM sources LIMIT 1").fetchone()[0])
            conn.execute(
                """INSERT INTO highlights(
                    source_id,unit_id,page,page_index,quote_text,anchor_type,text_prefix,text_exact,text_suffix,rects_json,opacity,label,note,color,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source_id, None, 3, 2, 'Q', 'bad_type', '', '', '', '', 9.9, '', '', '#2d9cdb', '2026-01-01T00:00:00+00:00', ''),
            )
            conn.commit()
            conn.close()

            db = Database(tmp.name)
            self.addCleanup(db.close)
            row = db.conn.execute("SELECT anchor_type,opacity,rects_json,updated_at FROM highlights ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row["anchor_type"], "text")
            self.assertAlmostEqual(float(row["opacity"]), 1.0, places=6)
            self.assertEqual(row["rects_json"], "[]")
            self.assertEqual(row["updated_at"], "2026-01-01T00:00:00+00:00")

    def test_repo_can_create_area_highlights(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            source_repo = SourceRepo(db)
            source_id = source_repo.create('Book', '/tmp/book.pdf', 10, 100)
            repo = HighlightRepo(db)

            rects = [{'x': 0.1, 'y': 0.2, 'w': 0.3, 'h': 0.1}]
            hid = repo.add_area_highlight(
                source_id=source_id,
                page=9,
                rects=rects,
                note='diagram',
                color='#e74c3c',
                quote_text='figure 1',
                opacity=0.5,
                label='Important',
            )

            row = db.conn.execute('SELECT * FROM highlights WHERE id=?', (hid,)).fetchone()
            self.assertEqual(row['anchor_type'], 'rect')
            self.assertEqual(row['page_index'], 8)
            self.assertEqual(json.loads(row['rects_json']), rects)
            self.assertEqual(row['label'], 'Important')
            self.assertAlmostEqual(float(row['opacity']), 0.5, places=6)

    def test_repo_text_highlight_supports_text_exact_and_exact_lookup(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            source_repo = SourceRepo(db)
            source_id = source_repo.create('Book', '/tmp/book.pdf', 10, 100)
            repo = HighlightRepo(db)

            hid = repo.add_text_highlight(
                source_id=source_id,
                page=12,
                quote_text='raw quote',
                text_prefix='before',
                text_exact='normalized quote',
                text_suffix='after',
                opacity=3.5,
            )
            row = db.conn.execute('SELECT * FROM highlights WHERE id=?', (hid,)).fetchone()
            self.assertEqual(row['anchor_type'], 'text')
            self.assertEqual(row['text_prefix'], 'before')
            self.assertEqual(row['text_exact'], 'normalized quote')
            self.assertEqual(row['text_suffix'], 'after')
            self.assertAlmostEqual(float(row['opacity']), 1.0, places=6)

            found = repo.find_exact(source_id=source_id, page=12, quote_text='normalized quote')
            self.assertIsNotNone(found)
            self.assertEqual(int(found['id']), hid)

    def test_repo_resolve_text_anchor_page_prefers_exact_then_fallback(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            source_repo = SourceRepo(db)
            source_id = source_repo.create('Book', '/tmp/book.pdf', 10, 100)
            repo = HighlightRepo(db)

            repo.add_text_highlight(source_id=source_id, page=5, quote_text='quote one', text_exact='Exact Alpha')
            repo.add_text_highlight(source_id=source_id, page=20, quote_text='quote two', text_exact='Exact Alpha')
            repo.add_text_highlight(source_id=source_id, page=33, quote_text='Fallback Quote', text_exact='Something Else')

            resolved = repo.resolve_text_anchor_page(source_id=source_id, text_exact='Exact Alpha', page_hint=18)
            self.assertEqual(resolved, 20)

            resolved_fallback = repo.resolve_text_anchor_page(source_id=source_id, text_exact='No Match', quote_text='Fallback Quote')
            self.assertEqual(resolved_fallback, 33)

    def test_repo_crud_and_anchor_serialization(self):
        with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            source_repo = SourceRepo(db)
            source_id = source_repo.create('Book', '/tmp/book.pdf', 10, 100)
            repo = HighlightRepo(db)

            hid = repo.add_text_highlight(
                source_id=source_id,
                page=14,
                quote_text='Some highlighted text',
                note='first note',
                color='#8b5cf6',
                text_prefix='Some',
                text_exact='Some highlighted text',
                text_suffix='text',
            )
            row = repo.get_highlight(hid)
            self.assertEqual(row['text_exact'], 'Some highlighted text')
            self.assertEqual(row['text_prefix'], 'Some')
            self.assertEqual(row['text_suffix'], 'text')

            repo.update_highlight_note(hid, 'updated note')
            repo.update_highlight_color(hid, '#27ae60')
            updated = repo.get_highlight(hid)
            self.assertEqual(updated['note'], 'updated note')
            self.assertEqual(updated['color'], '#27ae60')
            self.assertTrue(updated['updated_at'])

            repo.delete_highlight(hid)
            self.assertIsNone(repo.get_highlight(hid))


if __name__ == '__main__':
    unittest.main()

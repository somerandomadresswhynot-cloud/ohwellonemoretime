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


if __name__ == '__main__':
    unittest.main()

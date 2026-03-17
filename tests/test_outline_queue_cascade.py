import tempfile
import unittest

from study_app.persistence.database import Database
from study_app.persistence.repositories import OutlineRepo, SourceRepo


class OutlineQueueCascadeTests(unittest.TestCase):
    def test_parent_toggle_cascades_and_leaf_override_is_allowed(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            source_repo = SourceRepo(db)
            outline_repo = OutlineRepo(db)
            source_id = source_repo.create("Book", "/tmp/book.pdf", 1, 200)
            outline_repo.replace_outline(source_id, [
                {"depth": 1, "title": "Book", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
                {"depth": 2, "title": "Chapter 1", "start_page": 10, "end_page": 20, "is_unit": False, "queue_enabled": True},
                {"depth": 3, "title": "Topic A", "start_page": 10, "end_page": 12, "is_unit": True, "queue_enabled": True},
                {"depth": 3, "title": "Topic B", "start_page": 13, "end_page": 20, "is_unit": True, "queue_enabled": True},
            ])

            chapter_id = db.conn.execute("SELECT id FROM outline_nodes WHERE title='Chapter 1'").fetchone()["id"]
            topic_a_id = db.conn.execute("SELECT id FROM outline_nodes WHERE title='Topic A'").fetchone()["id"]
            topic_b_id = db.conn.execute("SELECT id FROM outline_nodes WHERE title='Topic B'").fetchone()["id"]

            outline_repo.set_queue_enabled(chapter_id, False)
            chapter_and_children = db.conn.execute(
                "SELECT title, queue_enabled FROM outline_nodes WHERE id IN (?,?,?) ORDER BY title",
                (chapter_id, topic_a_id, topic_b_id),
            ).fetchall()
            self.assertEqual([r["queue_enabled"] for r in chapter_and_children], [0, 0, 0])

            outline_repo.set_queue_enabled(topic_b_id, True)
            topic_states = db.conn.execute(
                "SELECT title, queue_enabled FROM outline_nodes WHERE id IN (?,?) ORDER BY title",
                (topic_a_id, topic_b_id),
            ).fetchall()
            self.assertEqual([r["queue_enabled"] for r in topic_states], [0, 1])


if __name__ == "__main__":
    unittest.main()

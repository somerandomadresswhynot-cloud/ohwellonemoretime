import tempfile
import unittest

from study_app.persistence.database import Database
from study_app.persistence.repositories import OutlineRepo, ReviewRepo, SourceRepo


class ReviewRepoAtomicTests(unittest.TestCase):
    def _seed_unit(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db")
        self.addCleanup(tmp.close)
        db = Database(tmp.name)
        self.addCleanup(db.close)
        source_repo = SourceRepo(db)
        outline_repo = OutlineRepo(db)
        review_repo = ReviewRepo(db)

        source_id = source_repo.create("Book", "/tmp/book.pdf", 1, 200)
        outline_repo.replace_outline(source_id, [
            {"depth": 1, "title": "Book", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
            {"depth": 2, "title": "Chapter 1", "start_page": 10, "end_page": 20, "is_unit": True, "queue_enabled": True},
        ])
        unit = db.conn.execute("SELECT * FROM units LIMIT 1").fetchone()
        return db, review_repo, unit

    def test_record_review_writes_event_and_updates_unit(self):
        db, review_repo, unit = self._seed_unit()

        payload = {
            "started_at": "2026-01-01T10:00:00",
            "ended_at": "2026-01-01T10:05:00",
            "elapsed_seconds": 300,
            "rating": "with_effort",
            "pre_note": "before",
            "post_note": "after",
        }
        stats = {
            "last_review_at": "2026-01-01T10:05:00",
            "review_count": 1,
            "ease_factor": 2.4,
            "avg_rating": 3.0,
        }

        event_id = review_repo.record_review(int(unit["id"]), payload, stats)

        event = db.conn.execute("SELECT * FROM review_events WHERE id=?", (event_id,)).fetchone()
        refreshed = db.conn.execute("SELECT * FROM units WHERE id=?", (unit["id"],)).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(refreshed["review_count"], 1)
        self.assertEqual(event["interval_days"], 0.0)
        self.assertEqual(event["next_review_at"], payload["ended_at"])

    def test_record_review_rolls_back_when_unit_update_fails(self):
        db, review_repo, unit = self._seed_unit()

        db.conn.execute(
            """CREATE TRIGGER delete_unit_after_event_insert
            AFTER INSERT ON review_events
            BEGIN
                DELETE FROM units WHERE id = NEW.unit_id;
            END;"""
        )
        db.conn.commit()

        payload = {
            "started_at": "2026-01-01T10:00:00",
            "ended_at": "2026-01-01T10:05:00",
            "elapsed_seconds": 300,
            "rating": "with_effort",
            "pre_note": "before",
            "post_note": "after",
        }
        stats = {
            "last_review_at": "2026-01-01T10:05:00",
            "review_count": 1,
            "ease_factor": 2.4,
            "avg_rating": 3.0,
        }

        with self.assertRaises(RuntimeError):
            review_repo.record_review(int(unit["id"]), payload, stats)

        events = db.conn.execute("SELECT COUNT(*) AS c FROM review_events").fetchone()["c"]
        existing_unit = db.conn.execute("SELECT * FROM units WHERE id=?", (unit["id"],)).fetchone()
        self.assertEqual(events, 0)
        self.assertIsNotNone(existing_unit)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest

from study_app.persistence.database import Database


class DatabaseIndexesTests(unittest.TestCase):
    REQUIRED_INDEXES = {
        "idx_outline_nodes_source_order",
        "idx_units_due_queue",
        "idx_units_source_queue",
        "idx_review_events_unit_deleted_ended",
        "idx_review_events_unit_deleted",
    }

    def _current_indexes(self, db: Database) -> set[str]:
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r["name"] for r in rows}

    def test_new_database_has_required_indexes(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            self.addCleanup(db.close)
            names = self._current_indexes(db)
            self.assertTrue(self.REQUIRED_INDEXES.issubset(names))

    def test_existing_database_is_upgraded_with_required_indexes(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            db.conn.execute("DROP INDEX IF EXISTS idx_outline_nodes_source_order")
            db.conn.execute("DROP INDEX IF EXISTS idx_units_due_queue")
            db.conn.execute("DROP INDEX IF EXISTS idx_units_source_queue")
            db.conn.execute("DROP INDEX IF EXISTS idx_review_events_unit_deleted_ended")
            db.conn.execute("DROP INDEX IF EXISTS idx_review_events_unit_deleted")
            db.conn.commit()
            db.close()

            upgraded = Database(tmp.name)
            self.addCleanup(upgraded.close)
            names = self._current_indexes(upgraded)
            self.assertTrue(self.REQUIRED_INDEXES.issubset(names))


if __name__ == "__main__":
    unittest.main()

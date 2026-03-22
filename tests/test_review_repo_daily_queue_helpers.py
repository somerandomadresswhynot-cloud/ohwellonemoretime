import tempfile
import unittest

from study_app.persistence.database import Database
from study_app.persistence.repositories import OutlineRepo, ReviewRepo, SourceRepo
from study_app.domain.models import utcnow_iso


class ReviewRepoDailyQueueHelpersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db')
        self.db = Database(self.tmp.name)
        self.addCleanup(self.db.close)
        self.addCleanup(self.tmp.close)
        self.source_repo = SourceRepo(self.db)
        self.outline_repo = OutlineRepo(self.db)
        self.review_repo = ReviewRepo(self.db)

        self.source_id = self.source_repo.create('Book', '/tmp/book.pdf', 100, 120)
        self.outline_repo.replace_outline(
            self.source_id,
            [
                {"depth": 1, "title": "Book", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
                {"depth": 2, "title": "U1", "start_page": 1, "end_page": 5, "is_unit": True, "queue_enabled": True},
                {"depth": 2, "title": "U2", "start_page": 6, "end_page": 10, "is_unit": True, "queue_enabled": True},
            ],
        )
        self.units = self.review_repo.source_units(self.source_id)

    def test_unit_views_by_ids_preserves_requested_order(self):
        first = int(self.units[0]['id'])
        second = int(self.units[1]['id'])
        rows = self.review_repo.unit_views_by_ids([second, first])
        self.assertEqual([int(r.unit_id) for r in rows], [second, first])

    def test_reviewed_unit_ids_on_date_returns_distinct_ids(self):
        unit_id = int(self.units[0]['id'])
        now = utcnow_iso()
        payload = {
            'started_at': now,
            'ended_at': now,
            'elapsed_seconds': 12,
            'rating': 'easy',
            'pre_note': '',
            'post_note': '',
            'interval_days': 1.0,
            'next_review_at': now,
        }
        stats = {
            'last_review_at': now,
            'next_review_at': now,
            'review_count': 1,
            'ease_factor': 2.5,
            'interval_days': 1.0,
            'avg_rating': 5.0,
        }
        self.review_repo.record_review(unit_id, payload, stats)
        day = now[:10]
        self.assertEqual(self.review_repo.review_count_on_date(day), 1)
        self.assertEqual(self.review_repo.reviewed_unit_ids_on_date(day), {unit_id})

    def test_first_unit_for_source_page_prefers_matching_unit(self):
        unit = self.review_repo.first_unit_for_source_page(self.source_id, 7)
        self.assertIsNotNone(unit)
        self.assertEqual(unit.title, 'U2')


if __name__ == '__main__':
    unittest.main()

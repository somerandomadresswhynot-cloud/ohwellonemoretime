import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from study_app.persistence.database import Database
from study_app.persistence.repositories import OutlineRepo, ReviewRepo, SourceRepo
from study_app.services.queue_planner import plan_session_queue
from study_app.services.runtime_estimator import build_runtime_estimation_model


@dataclass
class UnitStub:
    unit_id: int
    source_id: int
    start_page: int = 1
    end_page: int = 1
    review_count: int = 0
    next_review_at: str | None = None


class RuntimeEstimatorTests(unittest.TestCase):
    def _build_observations(self):
        # Stage progression for two sources. Source 2 is consistently slower.
        rows = []
        event_id = 1
        for source_id, scale in ((1, 1.0), (2, 2.0)):
            for unit_offset in range(2):
                unit_id = source_id * 100 + unit_offset
                for idx, elapsed in enumerate((180, 120, 90, 60)):
                    rows.append(
                        {
                            "event_id": event_id,
                            "unit_id": unit_id,
                            "source_id": source_id,
                            "ended_at": f"2026-03-{10 + idx:02d}T10:00:00+00:00",
                            "elapsed_seconds": elapsed * scale,
                            "rating": "easy",
                            "page_count": 1,
                        }
                    )
                    event_id += 1
        # Extremely sparse outlier source should be shrunk toward 1.0.
        rows.append(
            {
                "event_id": event_id,
                "unit_id": 999,
                "source_id": 99,
                "ended_at": "2026-03-20T10:00:00+00:00",
                "elapsed_seconds": 900,
                "rating": "easy",
                "page_count": 1,
            }
        )
        return rows

    def test_new_stage_is_slower_than_mature_stage(self):
        model = build_runtime_estimation_model(self._build_observations(), 60, 90)
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        new_unit = UnitStub(unit_id=1, source_id=1, review_count=0)
        mature_unit = UnitStub(unit_id=2, source_id=1, review_count=5)
        self.assertGreater(model.estimate_unit_seconds(new_unit, now), model.estimate_unit_seconds(mature_unit, now))

    def test_stale_reviews_cost_more_than_fresh_reviews(self):
        model = build_runtime_estimation_model(self._build_observations(), 60, 90)
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        fresh = UnitStub(unit_id=1, source_id=1, review_count=2, next_review_at="2026-03-22T10:00:00+00:00")
        stale = UnitStub(unit_id=2, source_id=1, review_count=2, next_review_at="2026-02-20T10:00:00+00:00")
        self.assertGreater(model.estimate_unit_seconds(stale, now), model.estimate_unit_seconds(fresh, now))

    def test_source_factor_changes_estimate_for_identical_units(self):
        model = build_runtime_estimation_model(self._build_observations(), 60, 90)
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        source_a = UnitStub(unit_id=1, source_id=1, review_count=3)
        source_b = UnitStub(unit_id=2, source_id=2, review_count=3)
        self.assertGreater(model.estimate_unit_seconds(source_b, now), model.estimate_unit_seconds(source_a, now))

    def test_sparse_source_is_shrunk_toward_global_baseline(self):
        model = build_runtime_estimation_model(self._build_observations(), 60, 90)
        sparse_factor = model.source_factors[99]
        self.assertGreater(sparse_factor, 1.0)
        self.assertLess(sparse_factor, 1.5)

    def test_queue_planner_uses_same_estimator_for_selection(self):
        model = build_runtime_estimation_model(self._build_observations(), 60, 90)
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        due = [
            UnitStub(unit_id=11, source_id=1, review_count=3),
            UnitStub(unit_id=12, source_id=2, review_count=3),
            UnitStub(unit_id=13, source_id=1, review_count=0),
        ]
        plan = plan_session_queue(due, available_minutes=4, estimate_seconds=lambda u: model.estimate_unit_seconds(u, now))
        selected_ids = [u.unit_id for u in plan.selected_units]
        self.assertIn(11, selected_ids)
        self.assertGreaterEqual(plan.overflow_count, 0)


class RuntimeEstimatorNoPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db')
        self.db = Database(self.tmp.name)
        self.addCleanup(self.db.close)
        self.addCleanup(self.tmp.close)
        self.source_repo = SourceRepo(self.db)
        self.outline_repo = OutlineRepo(self.db)
        self.review_repo = ReviewRepo(self.db)
        sid = self.source_repo.create('Book', '/tmp/book.pdf', 100, 120)
        self.outline_repo.replace_outline(
            sid,
            [
                {"depth": 1, "title": "Book", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
                {"depth": 2, "title": "U1", "start_page": 1, "end_page": 5, "is_unit": True, "queue_enabled": True},
            ],
        )
        unit_id = int(self.review_repo.source_units(sid)[0]["id"])
        payload = {
            'started_at': "2026-03-20T10:00:00+00:00",
            'ended_at': "2026-03-20T10:05:00+00:00",
            'elapsed_seconds': 60,
            'rating': 'easy',
            'pre_note': '',
            'post_note': '',
        }
        stats = {'last_review_at': payload['ended_at'], 'review_count': 1, 'ease_factor': 2.5, 'avg_rating': 5.0}
        self.review_repo.record_review(unit_id, payload, stats)

    def test_runtime_estimator_reads_without_db_writes(self):
        before = self.db.conn.total_changes
        observations = self.review_repo.runtime_estimation_observations()
        model = build_runtime_estimation_model(observations, 60, 90)
        _ = model.global_base_minutes_per_page
        _ = self.review_repo.due_units("2026-03-23T12:00:00+00:00")
        after = self.db.conn.total_changes
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()

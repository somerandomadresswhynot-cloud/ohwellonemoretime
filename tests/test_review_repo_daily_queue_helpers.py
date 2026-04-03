import tempfile
import unittest
from datetime import timedelta

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

    def test_unit_views_by_ids_includes_disabled_queue_units(self):
        first = int(self.units[0]['id'])
        self.db.conn.execute("UPDATE units SET queue_enabled=0 WHERE id=?", (first,))
        self.db.conn.commit()
        rows = self.review_repo.unit_views_by_ids([first])
        self.assertEqual([int(r.unit_id) for r in rows], [first])

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
        }
        stats = {
            'last_review_at': now,
            'review_count': 1,
            'ease_factor': 2.5,
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

    def test_first_unit_for_source_page_ignores_queue_enabled_for_manual_add(self):
        unit_id = int(self.units[1]['id'])
        self.db.conn.execute("UPDATE units SET queue_enabled=0 WHERE id=?", (unit_id,))
        self.db.conn.commit()
        unit = self.review_repo.first_unit_for_source_page(self.source_id, 7)
        self.assertIsNotNone(unit)
        self.assertEqual(int(unit.unit_id), unit_id)

    def test_set_unit_queue_enabled_updates_unit_and_outline_node(self):
        unit_id = int(self.units[0]["id"])
        node_id = int(self.units[0]["node_id"])

        changed = self.review_repo.set_unit_queue_enabled(unit_id, False)
        self.assertTrue(changed)

        unit_row = self.db.conn.execute("SELECT queue_enabled FROM units WHERE id=?", (unit_id,)).fetchone()
        node_row = self.db.conn.execute("SELECT queue_enabled FROM outline_nodes WHERE id=?", (node_id,)).fetchone()
        self.assertEqual(int(unit_row["queue_enabled"]), 0)
        self.assertEqual(int(node_row["queue_enabled"]), 0)

    def test_postponed_reviewed_unit_is_excluded_from_due_units(self):
        unit_id = int(self.units[0]["id"])
        ended_at = "2026-03-20T10:05:00+00:00"
        payload = {
            "started_at": ended_at,
            "ended_at": ended_at,
            "elapsed_seconds": 30,
            "rating": "with_effort",
            "pre_note": "",
            "post_note": "",
        }
        stats = {
            "last_review_at": ended_at,
            "review_count": 1,
            "ease_factor": 2.5,
            "avg_rating": 3.0,
        }
        self.review_repo.record_review(unit_id, payload, stats)
        due_now = {int(u.unit_id) for u in self.review_repo.due_units("2026-03-23T12:00:00+00:00")}
        self.assertIn(unit_id, due_now)

        changed = self.review_repo.postpone_unit_for(unit_id, timedelta(days=30))
        self.assertTrue(changed)
        due_after_postpone = {int(u.unit_id) for u in self.review_repo.due_units("2026-03-23T12:00:00+00:00")}
        self.assertNotIn(unit_id, due_after_postpone)

    def test_postponed_new_unit_is_excluded_from_new_units(self):
        unit_id = int(self.units[0]["id"])
        self.assertIn(unit_id, {int(u.unit_id) for u in self.review_repo.new_units("2026-03-23T12:00:00+00:00")})
        changed = self.review_repo.postpone_unit_for(unit_id, timedelta(days=2))
        self.assertTrue(changed)
        self.assertNotIn(unit_id, {int(u.unit_id) for u in self.review_repo.new_units("2026-03-23T12:00:00+00:00")})

    def test_clear_unit_postponement_restores_new_unit_visibility(self):
        unit_id = int(self.units[0]["id"])
        changed = self.review_repo.postpone_unit_for(unit_id, timedelta(days=2))
        self.assertTrue(changed)
        self.assertIn(unit_id, self.review_repo.active_postponed_unit_ids([unit_id], "2026-03-23T12:00:00+00:00"))
        self.review_repo.clear_unit_postponement(unit_id)
        self.assertNotIn(unit_id, self.review_repo.active_postponed_unit_ids([unit_id], "2026-03-23T12:00:00+00:00"))
        self.assertIn(unit_id, {int(u.unit_id) for u in self.review_repo.new_units("2026-03-23T12:00:00+00:00")})

    def test_missed_due_items_resurface_later(self):
        unit_id = int(self.units[0]['id'])
        payload = {
            'started_at': "2026-03-20T10:00:00+00:00",
            'ended_at': "2026-03-20T10:05:00+00:00",
            'elapsed_seconds': 30,
            'rating': 'with_effort',
            'pre_note': '',
            'post_note': '',
        }
        stats = {
            'last_review_at': "2026-03-20T10:05:00+00:00",
            'review_count': 1,
            'ease_factor': 2.5,
            'avg_rating': 3.0,
        }
        self.review_repo.record_review(unit_id, payload, stats)
        self.db.conn.execute(
            "UPDATE units SET next_review_at=? WHERE id=?",
            ("2099-01-01T00:00:00+00:00", unit_id),
        )
        self.db.conn.commit()
        due = self.review_repo.due_units("2026-03-23T12:00:00+00:00")
        self.assertIn(unit_id, {int(u.unit_id) for u in due})

    def test_due_units_excludes_never_reviewed_units(self):
        due = self.review_repo.due_units("2026-03-23T12:00:00+00:00")
        self.assertEqual(due, [])

    def test_review_summary_between_uses_half_open_window(self):
        unit_id = int(self.units[0]["id"])
        for ended_at in [
            "2026-03-22T23:59:59+00:00",
            "2026-03-23T00:00:00+00:00",
            "2026-03-23T12:00:00+00:00",
            "2026-03-24T00:00:00+00:00",
        ]:
            payload = {
                "started_at": ended_at,
                "ended_at": ended_at,
                "elapsed_seconds": 20,
                "rating": "easy",
                "pre_note": "",
                "post_note": "",
            }
            stats = {
                "last_review_at": ended_at,
                "review_count": 1,
                "ease_factor": 2.5,
                "avg_rating": 5.0,
            }
            self.review_repo.record_review(unit_id, payload, stats)
        summary = self.review_repo.review_summary_between("2026-03-23T00:00:00+00:00", "2026-03-24T00:00:00+00:00")
        self.assertEqual(summary["review_count"], 2)
        self.assertEqual(summary["total_seconds"], 40.0)

    def test_reviewed_unit_ids_between_ordered_tracks_last_event_order(self):
        unit_a = int(self.units[0]["id"])
        unit_b = int(self.units[1]["id"])
        events = [
            (unit_b, "2026-03-23T01:00:00+00:00"),
            (unit_a, "2026-03-23T02:00:00+00:00"),
            (unit_b, "2026-03-23T03:00:00+00:00"),
        ]
        for uid, ended_at in events:
            payload = {
                "started_at": ended_at,
                "ended_at": ended_at,
                "elapsed_seconds": 10,
                "rating": "easy",
                "pre_note": "",
                "post_note": "",
            }
            stats = {
                "last_review_at": ended_at,
                "review_count": 1,
                "ease_factor": 2.5,
                "avg_rating": 5.0,
            }
            self.review_repo.record_review(uid, payload, stats)
        ordered = self.review_repo.reviewed_unit_ids_between_ordered("2026-03-23T00:00:00+00:00", "2026-03-24T00:00:00+00:00")
        self.assertEqual(ordered, [unit_a, unit_b])

    def test_review_seconds_by_unit_between_sums_elapsed_seconds(self):
        unit_a = int(self.units[0]["id"])
        unit_b = int(self.units[1]["id"])
        events = [
            (unit_a, "2026-03-23T01:00:00+00:00", 15),
            (unit_a, "2026-03-23T01:30:00+00:00", 20),
            (unit_b, "2026-03-23T02:00:00+00:00", 40),
            (unit_b, "2026-03-24T00:00:00+00:00", 99),
        ]
        for uid, ended_at, elapsed in events:
            payload = {
                "started_at": ended_at,
                "ended_at": ended_at,
                "elapsed_seconds": elapsed,
                "rating": "easy",
                "pre_note": "",
                "post_note": "",
            }
            stats = {
                "last_review_at": ended_at,
                "review_count": 1,
                "ease_factor": 2.5,
                "avg_rating": 5.0,
            }
            self.review_repo.record_review(uid, payload, stats)
        totals = self.review_repo.review_seconds_by_unit_between("2026-03-23T00:00:00+00:00", "2026-03-24T00:00:00+00:00")
        self.assertEqual(totals[unit_a], 35.0)
        self.assertEqual(totals[unit_b], 40.0)

    def test_review_history_for_units_returns_active_history_only(self):
        unit_a = int(self.units[0]["id"])
        ended_at = "2026-03-23T01:00:00+00:00"
        payload = {
            "started_at": ended_at,
            "ended_at": ended_at,
            "elapsed_seconds": 10,
            "rating": "easy",
            "pre_note": "",
            "post_note": "",
        }
        stats = {
            "last_review_at": ended_at,
            "review_count": 1,
            "ease_factor": 2.5,
            "avg_rating": 5.0,
        }
        event_id = self.review_repo.record_review(unit_a, payload, stats)
        self.review_repo.soft_delete_event(event_id)
        history = self.review_repo.review_history_for_units([unit_a, int(self.units[1]["id"])])
        self.assertEqual(history.get(unit_a), None)

    def test_new_units_returns_only_never_reviewed(self):
        reviewed_id = int(self.units[0]['id'])
        payload = {
            'started_at': "2026-03-20T10:00:00+00:00",
            'ended_at': "2026-03-20T10:05:00+00:00",
            'elapsed_seconds': 30,
            'rating': 'with_effort',
            'pre_note': '',
            'post_note': '',
        }
        stats = {
            'last_review_at': "2026-03-20T10:05:00+00:00",
            'review_count': 1,
            'ease_factor': 2.5,
            'avg_rating': 3.0,
        }
        self.review_repo.record_review(reviewed_id, payload, stats)
        new_ids = {int(u.unit_id) for u in self.review_repo.new_units()}
        self.assertNotIn(reviewed_id, new_ids)

    def test_source_due_units_scopes_without_global_filtering(self):
        source2 = self.source_repo.create('Book 2', '/tmp/book2.pdf', 100, 120)
        self.outline_repo.replace_outline(
            source2,
            [
                {"depth": 1, "title": "Book 2", "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True},
                {"depth": 2, "title": "U3", "start_page": 1, "end_page": 5, "is_unit": True, "queue_enabled": True},
            ],
        )
        unit1 = int(self.units[0]['id'])
        unit2 = int(self.review_repo.source_units(source2)[0]['id'])
        for uid in [unit1, unit2]:
            payload = {
                'started_at': "2026-03-20T10:00:00+00:00",
                'ended_at': "2026-03-20T10:05:00+00:00",
                'elapsed_seconds': 30,
                'rating': 'with_effort',
                'pre_note': '',
                'post_note': '',
            }
            stats = {
                'last_review_at': "2026-03-20T10:05:00+00:00",
                'review_count': 1,
                'ease_factor': 2.5,
                'avg_rating': 3.0,
            }
            self.review_repo.record_review(uid, payload, stats)
        due_ids = {int(u.unit_id) for u in self.review_repo.source_due_units(source2, "2026-03-23T12:00:00+00:00")}
        self.assertEqual(due_ids, {unit2})

    def test_reviewed_unit_page_summary_between_distinct_units(self):
        unit_a = int(self.units[0]["id"])
        unit_b = int(self.units[1]["id"])
        events = [
            (unit_a, "2026-03-23T01:00:00+00:00"),
            (unit_a, "2026-03-23T02:00:00+00:00"),
            (unit_b, "2026-03-23T03:00:00+00:00"),
            (unit_b, "2026-03-24T00:00:00+00:00"),
        ]
        for uid, ended_at in events:
            payload = {
                "started_at": ended_at,
                "ended_at": ended_at,
                "elapsed_seconds": 10,
                "rating": "easy",
                "pre_note": "",
                "post_note": "",
            }
            stats = {
                "last_review_at": ended_at,
                "review_count": 1,
                "ease_factor": 2.5,
                "avg_rating": 5.0,
            }
            self.review_repo.record_review(uid, payload, stats)
        summary = self.review_repo.reviewed_unit_page_summary_between("2026-03-23T00:00:00+00:00", "2026-03-24T00:00:00+00:00")
        self.assertEqual(summary["reviewed_unit_count"], 2)
        # U1 (pages 1-5) + U2 (pages 6-10) => 5 + 5 pages
        self.assertEqual(summary["reviewed_pages_sum"], 10)

    def test_soft_delete_event_recomputes_unit_stats_to_new_when_history_empty(self):
        unit_id = int(self.units[0]["id"])
        ended_at = "2026-03-23T01:00:00+00:00"
        payload = {
            "started_at": ended_at,
            "ended_at": ended_at,
            "elapsed_seconds": 10,
            "rating": "easy",
            "pre_note": "",
            "post_note": "",
        }
        stats = {
            "last_review_at": ended_at,
            "review_count": 1,
            "ease_factor": 2.5,
            "avg_rating": 5.0,
        }
        event_id = self.review_repo.record_review(unit_id, payload, stats)
        self.review_repo.soft_delete_event(event_id)
        row = self.review_repo.unit_by_id(unit_id)
        self.assertEqual(int(row["review_count"]), 0)
        self.assertIsNone(row["last_review_at"])
        self.assertIsNone(row["next_review_at"])
        self.assertEqual(float(row["avg_rating"]), 0.0)


if __name__ == '__main__':
    unittest.main()

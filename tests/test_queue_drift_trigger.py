import unittest
from datetime import datetime, timezone, timedelta

from study_app.services.queue_drift import should_rebuild_for_estimate_drift


class QueueDriftTriggerTests(unittest.TestCase):
    def test_requires_review_activity_and_minimum_units(self):
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(
            should_rebuild_for_estimate_drift(
                previous_seconds=1800,
                current_seconds=2400,
                unit_count=5,
                reviewed_today=0,
                last_rebuild_at="",
                now=now,
            )
        )
        self.assertFalse(
            should_rebuild_for_estimate_drift(
                previous_seconds=1800,
                current_seconds=2400,
                unit_count=2,
                reviewed_today=2,
                last_rebuild_at="",
                now=now,
            )
        )

    def test_requires_absolute_and_relative_threshold(self):
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(
            should_rebuild_for_estimate_drift(
                previous_seconds=1800,
                current_seconds=2000,
                unit_count=5,
                reviewed_today=1,
                last_rebuild_at="",
                now=now,
            )
        )
        self.assertTrue(
            should_rebuild_for_estimate_drift(
                previous_seconds=1800,
                current_seconds=2400,
                unit_count=5,
                reviewed_today=1,
                last_rebuild_at="",
                now=now,
            )
        )

    def test_respects_cooldown_window(self):
        now = datetime(2026, 3, 23, 12, 0, tzinfo=timezone.utc)
        last_rebuild = (now - timedelta(seconds=30)).isoformat()
        self.assertFalse(
            should_rebuild_for_estimate_drift(
                previous_seconds=1800,
                current_seconds=2600,
                unit_count=5,
                reviewed_today=2,
                last_rebuild_at=last_rebuild,
                now=now,
            )
        )


if __name__ == "__main__":
    unittest.main()

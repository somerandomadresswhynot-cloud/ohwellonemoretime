import unittest
from datetime import datetime, timezone

from study_app.domain.models import iso_utc, parse_iso_to_utc, utcnow_iso
from study_app.services.scheduler import retention_estimate


class TimestampUtcTests(unittest.TestCase):
    def test_utcnow_iso_is_timezone_aware_utc(self):
        value = utcnow_iso()
        parsed = datetime.fromisoformat(value)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timezone.utc.utcoffset(parsed))

    def test_parse_iso_to_utc_handles_naive_and_aware(self):
        naive = parse_iso_to_utc("2026-01-01T10:00:00")
        aware = parse_iso_to_utc("2026-01-01T10:00:00+02:00")
        self.assertEqual(naive.tzinfo, timezone.utc)
        self.assertEqual(aware.tzinfo, timezone.utc)
        self.assertEqual(aware.hour, 8)

    def test_retention_estimate_accepts_legacy_naive_timestamp(self):
        unit = {"last_review_at": "2026-01-01T10:00:00", "interval_days": 2}
        now = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
        score = retention_estimate(unit, now)
        self.assertGreater(score, 0)

    def test_iso_utc_normalizes_to_utc_offset(self):
        dt = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(iso_utc(dt), "2026-01-01T10:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

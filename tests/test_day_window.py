import unittest
from datetime import datetime, timezone

from study_app.services.day_window import day_window_for_offset, is_valid_gmt_offset, normalized_gmt_offset


class DayWindowTests(unittest.TestCase):
    def test_offset_validation(self):
        self.assertTrue(is_valid_gmt_offset("+00:00"))
        self.assertTrue(is_valid_gmt_offset("-05:30"))
        self.assertFalse(is_valid_gmt_offset("UTC+1"))
        self.assertFalse(is_valid_gmt_offset("+5:00"))

    def test_day_window_half_open_with_positive_offset(self):
        now = datetime(2026, 3, 23, 0, 30, tzinfo=timezone.utc)
        window = day_window_for_offset("+05:30", now)
        self.assertEqual(window.day_key, "2026-03-23")
        self.assertEqual(window.utc_start_iso, "2026-03-22T18:30:00+00:00")
        self.assertEqual(window.utc_next_start_iso, "2026-03-23T18:30:00+00:00")

    def test_day_window_half_open_with_negative_offset(self):
        now = datetime(2026, 3, 23, 3, 0, tzinfo=timezone.utc)
        window = day_window_for_offset("-05:00", now)
        self.assertEqual(window.day_key, "2026-03-22")
        self.assertEqual(window.utc_start_iso, "2026-03-22T05:00:00+00:00")
        self.assertEqual(window.utc_next_start_iso, "2026-03-23T05:00:00+00:00")

    def test_normalized_default(self):
        self.assertEqual(normalized_gmt_offset(None), "+00:00")
        self.assertEqual(normalized_gmt_offset("bad"), "+00:00")


if __name__ == "__main__":
    unittest.main()

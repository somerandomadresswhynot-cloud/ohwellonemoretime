import unittest

from study_app.services.time_format import format_elapsed_seconds, format_minutes_whole, round_minutes


class TimeFormatTests(unittest.TestCase):
    def test_zero_seconds_stays_zero(self):
        self.assertEqual(format_elapsed_seconds(0), "0s")
        self.assertEqual(format_elapsed_seconds(None), "0s")

    def test_seconds_and_minutes(self):
        self.assertEqual(format_elapsed_seconds(12), "12s")
        self.assertEqual(format_elapsed_seconds(90), "1.5 min")

    def test_whole_minute_rounding(self):
        self.assertEqual(round_minutes(0), 0)
        self.assertEqual(round_minutes(29), 0)
        self.assertEqual(round_minutes(30), 0)
        self.assertEqual(round_minutes(31), 1)
        self.assertEqual(format_minutes_whole(125), "2 min")


if __name__ == "__main__":
    unittest.main()

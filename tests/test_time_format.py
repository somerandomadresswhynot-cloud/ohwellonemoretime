import unittest

from study_app.services.time_format import format_elapsed_seconds


class TimeFormatTests(unittest.TestCase):
    def test_zero_seconds_stays_zero(self):
        self.assertEqual(format_elapsed_seconds(0), "0s")
        self.assertEqual(format_elapsed_seconds(None), "0s")

    def test_seconds_and_minutes(self):
        self.assertEqual(format_elapsed_seconds(12), "12s")
        self.assertEqual(format_elapsed_seconds(90), "1.5 min")


if __name__ == "__main__":
    unittest.main()

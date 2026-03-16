import unittest

from study_app.services.scheduler import allocate_new_units


class SchedulerAllocationTests(unittest.TestCase):
    def test_reserves_review_budget_first(self):
        out = allocate_new_units(due_review_minutes=80, daily_minutes=60, avg_new_unit_seconds=120, new_units_cap=10)
        self.assertEqual(out.free_minutes, 0.0)
        self.assertEqual(out.suggested_new_units, 0)
        self.assertTrue(out.review_only)

    def test_respects_new_units_cap(self):
        out = allocate_new_units(due_review_minutes=10, daily_minutes=60, avg_new_unit_seconds=30, new_units_cap=3)
        self.assertEqual(out.suggested_new_units, 3)
        self.assertFalse(out.review_only)

    def test_converts_free_time_to_unit_budget(self):
        out = allocate_new_units(due_review_minutes=25, daily_minutes=40, avg_new_unit_seconds=120, new_units_cap=20)
        self.assertEqual(out.free_minutes, 15.0)
        self.assertEqual(out.suggested_new_units, 7)


if __name__ == "__main__":
    unittest.main()

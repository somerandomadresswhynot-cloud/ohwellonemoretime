import unittest
from dataclasses import dataclass

from study_app.services.queue_planner import plan_session_queue


@dataclass
class FakeUnit:
    name: str
    sec: float


class QueuePlannerTests(unittest.TestCase):
    def test_fits_units_up_to_budget_in_order(self):
        due = [FakeUnit("A", 120), FakeUnit("B", 300), FakeUnit("C", 60)]
        plan = plan_session_queue(due, available_minutes=7, estimate_seconds=lambda u: u.sec)
        self.assertEqual([u.name for u in plan.selected_units], ["A", "B"])
        self.assertEqual(plan.overflow_count, 1)
        self.assertAlmostEqual(plan.projected_minutes, 7.0)

    def test_overflow_all_when_budget_is_zero(self):
        due = [FakeUnit("A", 120), FakeUnit("B", 60)]
        plan = plan_session_queue(due, available_minutes=0, estimate_seconds=lambda u: u.sec)
        self.assertEqual(plan.selected_units, [])
        self.assertEqual(plan.overflow_count, 2)
        self.assertEqual(plan.projected_minutes, 0.0)

    def test_uses_minimum_estimate_floor(self):
        due = [FakeUnit("A", 0), FakeUnit("B", -20), FakeUnit("C", 1)]
        plan = plan_session_queue(due, available_minutes=1, estimate_seconds=lambda u: u.sec)
        self.assertEqual([u.name for u in plan.selected_units], ["A", "B", "C"])
        self.assertEqual(plan.overflow_count, 0)
        self.assertAlmostEqual(plan.projected_minutes, 3 / 60)


if __name__ == "__main__":
    unittest.main()

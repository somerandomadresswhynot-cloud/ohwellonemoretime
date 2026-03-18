import unittest
from dataclasses import dataclass

from study_app.services.queue_planner import plan_session_queue


@dataclass
class FakeUnit:
    name: str
    sec: float
    source_id: int = 1


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

    def test_skips_oversized_unit_and_keeps_scanning(self):
        due = [FakeUnit("A", 60), FakeUnit("B", 600), FakeUnit("C", 60), FakeUnit("D", 60)]
        plan = plan_session_queue(due, available_minutes=4, estimate_seconds=lambda u: u.sec)
        self.assertEqual([u.name for u in plan.selected_units], ["A", "C", "D"])
        self.assertEqual(plan.overflow_count, 1)
        self.assertAlmostEqual(plan.projected_minutes, 3.0)
        self.assertEqual(plan.suggested_units[0].unit.name, "B")

    def test_strict_sources_get_progression_gate_suggestion(self):
        due = [
            FakeUnit("S1-first", 600, source_id=1),
            FakeUnit("S2-small", 60, source_id=2),
            FakeUnit("S1-second", 60, source_id=1),
        ]
        plan = plan_session_queue(
            due,
            available_minutes=2,
            estimate_seconds=lambda u: u.sec,
            strict_progression_sources={1},
            source_id_of=lambda u: u.source_id,
        )
        reasons = {s.reason for s in plan.suggested_units}
        self.assertIn("strict_order_progression_gate", reasons)
        self.assertEqual(plan.suggested_units[0].unit.name, "S1-first")


if __name__ == "__main__":
    unittest.main()

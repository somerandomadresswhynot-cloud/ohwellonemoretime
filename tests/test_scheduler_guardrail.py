import unittest

from study_app.services.scheduler import recommend_new_units_with_guardrail


class SchedulerGuardrailTests(unittest.TestCase):
    def test_allows_time_limited_when_future_load_safe(self):
        out = recommend_new_units_with_guardrail(
            due_review_minutes=20,
            daily_minutes=90,
            avg_new_unit_seconds=120,
            new_units_cap=10,
            horizon_days=10,
            safety_threshold=0.9,
        )
        self.assertGreater(out.recommended_new_units, 0)
        self.assertIsNone(out.limiting_day)

    def test_caps_when_future_projection_exceeds_threshold(self):
        out = recommend_new_units_with_guardrail(
            due_review_minutes=50,
            daily_minutes=60,
            avg_new_unit_seconds=600,
            new_units_cap=10,
            horizon_days=10,
            safety_threshold=0.9,
        )
        self.assertLess(out.recommended_new_units, 1)
        self.assertIsNotNone(out.limiting_day)
        self.assertGreater(out.limiting_projected_minutes, 54.0)


if __name__ == "__main__":
    unittest.main()

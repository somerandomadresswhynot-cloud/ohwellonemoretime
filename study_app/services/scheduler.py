from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

RATING_FACTORS = {
    "easy": 1.8,
    "with_effort": 1.25,
    "hard": 0.8,
    "skip": 0.35,
}

RATING_SCORE = {"easy": 5, "with_effort": 3, "hard": 2, "skip": 1}


@dataclass
class ScheduleResult:
    interval_days: float
    next_review_at: str
    ease_factor: float
    retention: float


def compute_next(unit_row, rating: str, now: datetime) -> ScheduleResult:
    prev_interval = float(unit_row["interval_days"] or 0)
    ef = float(unit_row["ease_factor"] or 2.5)
    review_count = int(unit_row["review_count"] or 0)

    if review_count == 0:
        interval = {"easy": 3, "with_effort": 1, "hard": 0.5, "skip": 0.2}[rating]
    else:
        interval = max(0.2, prev_interval * RATING_FACTORS[rating] * (ef / 2.5))

    ef = min(3.0, max(1.3, ef + (RATING_SCORE[rating] - 3) * 0.08))
    next_review = now + timedelta(days=interval)
    retention = retention_estimate(unit_row, now)
    return ScheduleResult(interval_days=interval, next_review_at=next_review.isoformat(timespec="seconds"), ease_factor=ef, retention=retention)


def retention_estimate(unit_row, now: datetime) -> float:
    last = unit_row["last_review_at"]
    if not last:
        return 0.15
    last_dt = datetime.fromisoformat(last)
    elapsed_days = max(0.0, (now - last_dt).total_seconds() / 86400)
    interval = float(unit_row["interval_days"] or 1)
    score = pow(2.71828, -elapsed_days / max(0.3, interval))
    return max(0.01, min(0.99, score))


def choose_new_units_allowed(due_count: int, daily_minutes: int, avg_review_seconds: float) -> bool:
    due_cost = due_count * max(avg_review_seconds, 45) / 60
    projected = due_cost * 1.3
    return projected < daily_minutes * 0.85

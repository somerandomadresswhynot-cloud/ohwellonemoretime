from datetime import datetime, timedelta, timezone

from study_app.services.scheduler import _round_due_to_local_day_start_utc, compute_next


def _first_review_row():
    return {
        "interval_days": 0,
        "ease_factor": 2.5,
        "review_count": 0,
        "last_review_at": None,
    }


def test_easy_first_review_rounds_to_day_start_in_utc():
    now = datetime(2026, 3, 10, 15, 45, tzinfo=timezone.utc)
    result = compute_next(_first_review_row(), "easy", now)
    assert result.next_review_at == "2026-03-13T00:00:00+00:00"


def test_rounding_uses_local_timezone_day_start_then_converts_to_utc():
    tz = timezone(timedelta(hours=-5))
    dt = datetime(2026, 3, 13, 13, 0, tzinfo=timezone.utc)
    rounded = _round_due_to_local_day_start_utc(dt, tz)
    assert rounded == datetime(2026, 3, 13, 5, 0, tzinfo=timezone.utc)

from datetime import datetime, timedelta, timezone

from study_app.services.fsrs_scheduler import (
    DEFAULT_FSRS_PARAMETERS,
    FSRSGrade,
    FSRSState,
    _round_due_to_local_day_start_utc,
    compute_retrievability,
    grade_from_feedback,
    interval_for_target_retention,
    next_state_from_review,
    replay_history_into_state,
    schedule_next_review,
)


def _unit_row_without_fsrs():
    return {
        "id": 1,
        "ease_factor": 2.5,
        "fsrs_difficulty": None,
        "fsrs_stability": None,
        "fsrs_last_review_at": None,
        "fsrs_last_grade": None,
        "fsrs_review_count": None,
        "fsrs_lapse_count": None,
        "fsrs_state_version": None,
    }


def test_feedback_grade_mapping():
    assert grade_from_feedback("skip") == FSRSGrade.AGAIN
    assert grade_from_feedback("hard") == FSRSGrade.HARD
    assert grade_from_feedback("with_effort") == FSRSGrade.GOOD
    assert grade_from_feedback("easy") == FSRSGrade.EASY


def test_replay_history_into_state_builds_state():
    events = [
        {"ended_at": "2026-03-10T10:00:00+00:00", "rating": "with_effort"},
        {"ended_at": "2026-03-13T10:00:00+00:00", "rating": "easy"},
    ]
    state = replay_history_into_state(events, DEFAULT_FSRS_PARAMETERS)
    assert state is not None
    assert state.review_count == 2
    assert state.stability > 0


def test_failed_recall_is_counted_as_lapse():
    prev = FSRSState(
        difficulty=5.0,
        stability=3.0,
        last_review_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        last_grade=FSRSGrade.GOOD,
        review_count=5,
        lapse_count=0,
    )
    state, _ = next_state_from_review(prev, elapsed_days=2.0, grade=FSRSGrade.AGAIN, params=DEFAULT_FSRS_PARAMETERS, reviewed_at=datetime(2026, 3, 12, tzinfo=timezone.utc))
    assert state.lapse_count == 1


def test_successful_recall_increases_stability():
    prev = FSRSState(
        difficulty=5.0,
        stability=3.0,
        last_review_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        last_grade=FSRSGrade.GOOD,
        review_count=5,
        lapse_count=0,
    )
    state, _ = next_state_from_review(prev, elapsed_days=2.0, grade=FSRSGrade.EASY, params=DEFAULT_FSRS_PARAMETERS, reviewed_at=datetime(2026, 3, 12, tzinfo=timezone.utc))
    assert state.stability > prev.stability


def test_interval_changes_with_desired_retention():
    state = FSRSState(
        difficulty=5.0,
        stability=10.0,
        last_review_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        last_grade=FSRSGrade.GOOD,
        review_count=5,
        lapse_count=0,
    )
    low_ret = interval_for_target_retention(state, 0.85)
    high_ret = interval_for_target_retention(state, 0.95)
    assert low_ret > high_ret


def test_timezone_rounding_to_local_day_start():
    tz = timezone(timedelta(hours=-5))
    dt = datetime(2026, 3, 13, 13, 0, tzinfo=timezone.utc)
    rounded = _round_due_to_local_day_start_utc(dt, tz)
    assert rounded == datetime(2026, 3, 13, 5, 0, tzinfo=timezone.utc)


def test_old_row_without_fsrs_still_schedules():
    now = datetime(2026, 3, 10, 15, 45, tzinfo=timezone.utc)
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=[],
        now=now,
        feedback="easy",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days >= 1.0
    assert out.next_review_at.endswith("+00:00")


def test_retrievability_bounds():
    r = compute_retrievability(10.0, 0.0)
    assert 0.99 <= r <= 1.0


def test_no_reviews_uses_normal_fsrs_instead_of_forced_next_day():
    now = datetime(2026, 3, 10, 15, 45, tzinfo=timezone.utc)
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=[],
        now=now,
        feedback="with_effort",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days > 1.0
    assert out.next_review_at == "2026-03-14T00:00:00+00:00"


def test_first_hard_review_forces_next_day():
    now = datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-19T08:30:00+00:00", "rating": "hard"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="hard",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days == 1.0
    assert out.next_review_at == "2026-03-21T00:00:00+00:00"


def test_first_good_review_forces_next_day():
    now = datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-19T08:30:00+00:00", "rating": "with_effort"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="with_effort",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days == 1.0


def test_first_again_review_forces_next_day():
    now = datetime(2026, 3, 20, 8, 30, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-19T08:30:00+00:00", "rating": "skip"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="skip",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days == 1.0


def test_hard_then_easy_uses_normal_fsrs_path():
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-20T12:00:00+00:00", "rating": "hard"},
        {"ended_at": "2026-03-27T12:00:00+00:00", "rating": "easy"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="easy",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days > 1.0


def test_again_then_easy_uses_normal_fsrs_path():
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-20T12:00:00+00:00", "rating": "skip"},
        {"ended_at": "2026-03-27T12:00:00+00:00", "rating": "easy"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="easy",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days > 1.0


def test_easy_then_easy_second_step_uses_normal_fsrs_path():
    now = datetime(2026, 3, 30, 12, 0, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-20T12:00:00+00:00", "rating": "easy"},
        {"ended_at": "2026-03-27T12:00:00+00:00", "rating": "easy"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="easy",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days > 1.0


def test_regression_mixed_history_hard_then_easy_not_forced_next_day():
    now = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
    history = [
        {"ended_at": "2026-03-23T12:00:00+00:00", "rating": "hard"},
        {"ended_at": "2026-03-30T12:00:00+00:00", "rating": "easy"},
    ]
    out = schedule_next_review(
        unit_row=_unit_row_without_fsrs(),
        review_events=history,
        now=now,
        feedback="easy",
        timezone_info=timezone.utc,
        desired_retention=0.9,
    )
    assert out.scheduled_interval_days > 1.0

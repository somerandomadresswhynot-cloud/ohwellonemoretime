from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
import math
from typing import Iterable

from study_app.domain.models import iso_utc, parse_iso_to_utc

# FSRS references:
# - https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm
# - https://github.com/open-spaced-repetition/ts-fsrs
# This implementation uses the standard DSR-style state transition equations used by
# the open-spaced-repetition project (difficulty/stability/retrievability model).


class FSRSGrade(IntEnum):
    AGAIN = 1
    HARD = 2
    GOOD = 3
    EASY = 4


@dataclass(frozen=True)
class FSRSParameters:
    # 19-parameter vector used by FSRS v4/4.5 family implementations.
    w: tuple[float, ...]


DEFAULT_FSRS_PARAMETERS = FSRSParameters(
    # Anki/FSRS standard default preset style parameter vector.
    w=(
        0.4872,
        1.4003,
        3.7145,
        13.8206,
        5.1618,
        1.2298,
        0.8975,
        0.031,
        1.6474,
        0.1367,
        1.0461,
        2.1072,
        0.0793,
        0.3246,
        1.587,
        0.2272,
        2.8755,
        0.1542,
        1.0824,
    )
)


@dataclass(frozen=True)
class FSRSState:
    difficulty: float
    stability: float
    last_review_at: datetime
    last_grade: FSRSGrade
    review_count: int
    lapse_count: int
    state_version: int = 1


@dataclass(frozen=True)
class SchedulingResult:
    state: FSRSState
    raw_interval_days: float
    scheduled_interval_days: float
    next_review_at: str
    due_retention_used: float
    retrievability_at_review: float


DECAY = -0.5
FACTOR = 19.0 / 81.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def grade_from_feedback(feedback: str) -> FSRSGrade:
    mapping = {
        "skip": FSRSGrade.AGAIN,
        "hard": FSRSGrade.HARD,
        "with_effort": FSRSGrade.GOOD,
        "easy": FSRSGrade.EASY,
    }
    if feedback not in mapping:
        raise ValueError(f"Unsupported feedback '{feedback}'")
    return mapping[feedback]


def compute_retrievability(stability_days: float, elapsed_days: float) -> float:
    s = max(0.01, float(stability_days))
    t = max(0.0, float(elapsed_days))
    return _clamp((1.0 + FACTOR * t / s) ** DECAY, 0.0, 1.0)


def _initial_stability(grade: FSRSGrade, p: FSRSParameters) -> float:
    return max(0.1, float(p.w[int(grade) - 1]))


def _initial_difficulty(grade: FSRSGrade, p: FSRSParameters) -> float:
    # Standard FSRS initialization formula.
    return _clamp(float(p.w[4]) - math.exp(float(p.w[5]) * (float(grade) - 1.0)) + 1.0, 1.0, 10.0)


def _mean_reversion(value: float, init: float, p: FSRSParameters) -> float:
    return float(p.w[7]) * init + (1.0 - float(p.w[7])) * value


def _next_difficulty(prev_d: float, grade: FSRSGrade, p: FSRSParameters) -> float:
    d = float(prev_d) - float(p.w[6]) * (float(grade) - 3.0)
    return _clamp(_mean_reversion(d, _initial_difficulty(FSRSGrade.EASY, p), p), 1.0, 10.0)


def _next_recall_stability(prev_d: float, prev_s: float, r: float, grade: FSRSGrade, p: FSRSParameters) -> float:
    hard_penalty = float(p.w[15]) if grade == FSRSGrade.HARD else 1.0
    easy_bonus = float(p.w[16]) if grade == FSRSGrade.EASY else 1.0
    growth = math.exp(float(p.w[8])) * (11.0 - prev_d) * (prev_s ** (-float(p.w[9]))) * (math.exp((1.0 - r) * float(p.w[10])) - 1.0)
    return max(0.1, prev_s * (1.0 + growth * hard_penalty * easy_bonus))


def _next_forget_stability(prev_d: float, prev_s: float, r: float, p: FSRSParameters) -> float:
    return max(
        0.1,
        float(p.w[11])
        * (prev_d ** (-float(p.w[12])))
        * ((prev_s + 1.0) ** float(p.w[13]) - 1.0)
        * math.exp((1.0 - r) * float(p.w[14])),
    )


def next_state_from_review(prev_state: FSRSState | None, elapsed_days: float, grade: FSRSGrade, params: FSRSParameters = DEFAULT_FSRS_PARAMETERS, reviewed_at: datetime | None = None) -> tuple[FSRSState, float]:
    reviewed_at = reviewed_at or datetime.now(timezone.utc)
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)

    if prev_state is None:
        s = _initial_stability(grade, params)
        d = _initial_difficulty(grade, params)
        return (
            FSRSState(
                difficulty=d,
                stability=s,
                last_review_at=reviewed_at,
                last_grade=grade,
                review_count=1,
                lapse_count=1 if grade == FSRSGrade.AGAIN else 0,
                state_version=1,
            ),
            1.0,
        )

    r = compute_retrievability(prev_state.stability, elapsed_days)
    d = _next_difficulty(prev_state.difficulty, grade, params)
    if grade == FSRSGrade.AGAIN:
        s = _next_forget_stability(prev_state.difficulty, prev_state.stability, r, params)
    else:
        s = _next_recall_stability(prev_state.difficulty, prev_state.stability, r, grade, params)

    return (
        FSRSState(
            difficulty=d,
            stability=s,
            last_review_at=reviewed_at,
            last_grade=grade,
            review_count=max(0, int(prev_state.review_count)) + 1,
            lapse_count=max(0, int(prev_state.lapse_count)) + (1 if grade == FSRSGrade.AGAIN else 0),
            state_version=max(1, int(prev_state.state_version or 1)),
        ),
        r,
    )


def interval_for_target_retention(state: FSRSState, desired_retention: float, _params: FSRSParameters = DEFAULT_FSRS_PARAMETERS) -> float:
    r = _clamp(desired_retention, 0.7, 0.99)
    # Inverse of R(t,S) = (1 + FACTOR * t / S)^DECAY
    interval = (state.stability / FACTOR) * (pow(r, 1.0 / DECAY) - 1.0)
    return max(0.03, float(interval))


def _round_due_to_local_day_start_utc(dt: datetime, tzinfo=None) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if tzinfo is None:
        tzinfo = datetime.now().astimezone().tzinfo or timezone.utc
    local_dt = dt.astimezone(tzinfo)
    local_midnight = datetime(local_dt.year, local_dt.month, local_dt.day, tzinfo=tzinfo)
    return local_midnight.astimezone(timezone.utc)


def replay_history_into_state(review_events: Iterable, params: FSRSParameters = DEFAULT_FSRS_PARAMETERS) -> FSRSState | None:
    state: FSRSState | None = None
    last_review_at: datetime | None = None
    for ev in sorted(review_events, key=lambda r: str(r["ended_at"])):
        grade = grade_from_feedback(str(ev["rating"]))
        reviewed_at = parse_iso_to_utc(str(ev["ended_at"]))
        elapsed = 0.0 if last_review_at is None else max(0.0, (reviewed_at - last_review_at).total_seconds() / 86400.0)
        state, _ = next_state_from_review(state, elapsed, grade, params, reviewed_at=reviewed_at)
        last_review_at = reviewed_at
    return state


def should_force_next_day_for_first_review(review_history: list) -> bool:
    # Special-case only the first completed review in a unit's history.
    # Once history has 0 or >=2 records, use normal FSRS interval unchanged.
    return len(review_history) == 1


def schedule_next_review(
    unit_row,
    review_events,
    now: datetime,
    feedback: str,
    timezone_info,
    desired_retention: float = 0.9,
    params: FSRSParameters = DEFAULT_FSRS_PARAMETERS,
) -> SchedulingResult:
    row = unit_row if isinstance(unit_row, dict) else dict(unit_row)
    history = list(review_events)
    grade = grade_from_feedback(feedback)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    prior_state: FSRSState | None = None
    if row.get("fsrs_stability") is not None and row.get("fsrs_difficulty") is not None and row.get("fsrs_last_review_at"):
        try:
            prior_state = FSRSState(
                difficulty=float(row["fsrs_difficulty"]),
                stability=float(row["fsrs_stability"]),
                last_review_at=parse_iso_to_utc(str(row["fsrs_last_review_at"])),
                last_grade=FSRSGrade(int(row["fsrs_last_grade"] or FSRSGrade.GOOD)),
                review_count=int(row["fsrs_review_count"] or 0),
                lapse_count=int(row["fsrs_lapse_count"] or 0),
                state_version=int(row["fsrs_state_version"] or 1),
            )
        except Exception:
            prior_state = None

    if prior_state is None:
        prior_state = replay_history_into_state(history, params)

    elapsed = 0.0
    if prior_state is not None:
        elapsed = max(0.0, (now - prior_state.last_review_at).total_seconds() / 86400.0)

    new_state, review_retrievability = next_state_from_review(prior_state, elapsed, grade, params, reviewed_at=now)
    raw_interval = interval_for_target_retention(new_state, desired_retention, params)
    scheduled_interval = 1.0 if should_force_next_day_for_first_review(history) else raw_interval
    due_dt = now + timedelta(days=scheduled_interval)
    due_dt = _round_due_to_local_day_start_utc(due_dt, timezone_info)

    return SchedulingResult(
        state=new_state,
        raw_interval_days=raw_interval,
        scheduled_interval_days=scheduled_interval,
        next_review_at=iso_utc(due_dt),
        due_retention_used=_clamp(desired_retention, 0.7, 0.99),
        retrievability_at_review=review_retrievability,
    )

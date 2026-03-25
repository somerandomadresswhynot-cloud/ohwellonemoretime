from __future__ import annotations

from datetime import datetime

from study_app.domain.models import parse_iso_to_utc

DRIFT_RELATIVE_THRESHOLD = 0.15
DRIFT_ABSOLUTE_SECONDS = 5 * 60.0
DRIFT_COOLDOWN_SECONDS = 120.0
DRIFT_MIN_UNIT_COUNT = 3


def should_rebuild_for_estimate_drift(
    *,
    previous_seconds: float | None,
    current_seconds: float,
    unit_count: int,
    reviewed_today: int,
    last_rebuild_at: str,
    now: datetime,
) -> bool:
    if previous_seconds is None:
        return False
    if reviewed_today <= 0:
        return False
    if unit_count < DRIFT_MIN_UNIT_COUNT:
        return False
    if last_rebuild_at:
        try:
            elapsed_since_rebuild = (now - parse_iso_to_utc(last_rebuild_at)).total_seconds()
            if elapsed_since_rebuild < DRIFT_COOLDOWN_SECONDS:
                return False
        except Exception:
            pass
    prev = max(1.0, float(previous_seconds))
    curr = max(0.0, float(current_seconds))
    drift_abs = abs(curr - prev)
    drift_rel = drift_abs / prev
    return drift_abs >= DRIFT_ABSOLUTE_SECONDS and drift_rel >= DRIFT_RELATIVE_THRESHOLD

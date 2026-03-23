from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from study_app.domain.models import parse_iso_to_utc
from study_app.persistence.repositories import _derive_due_at_from_events


_STAGE_NEW = "new"
_STAGE_ONE = "one_prior"
_STAGE_TWO = "two_prior"
_STAGE_MATURE = "mature"
_STAGE_ORDER = (_STAGE_NEW, _STAGE_ONE, _STAGE_TWO, _STAGE_MATURE)

_STALE_FRESH = "fresh"
_STALE_LIGHT = "light"
_STALE_MEDIUM = "medium"
_STALE_HEAVY = "heavy"
_STALE_BUCKET_ORDER = (_STALE_FRESH, _STALE_LIGHT, _STALE_MEDIUM, _STALE_HEAVY)

_DEFAULT_STAGE_FACTORS = {
    _STAGE_NEW: 1.35,
    _STAGE_ONE: 1.15,
    _STAGE_TWO: 1.0,
    _STAGE_MATURE: 0.92,
}

_DEFAULT_STALE_FACTORS = {
    _STALE_FRESH: 1.0,
    _STALE_LIGHT: 1.08,
    _STALE_MEDIUM: 1.2,
    _STALE_HEAVY: 1.35,
}


@dataclass(frozen=True)
class RuntimeEstimationModel:
    """Runtime-only estimate model derived from review history and settings.

    The model decomposes estimate cost into:
    - global minutes-per-page baseline
    - source factor (with shrinkage)
    - stage factor (global)
    - stale factor (global)
    - optional unit-specific factor (only when stable)
    """

    global_base_minutes_per_page: float
    source_factors: dict[int, float]
    stage_factors: dict[str, float]
    stale_bucket_factors: dict[str, float]
    stale_cap: float
    fallback_seconds_per_page: float
    fallback_seconds_per_unit: float
    unit_factors: dict[int, float] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)

    def estimate_unit_seconds(self, unit: Any, now: datetime) -> float:
        pages = _safe_page_count(unit)
        prior_reviews = max(0, int(getattr(unit, "review_count", 0) or 0))

        stage = _stage_bucket(prior_reviews)
        stage_factor = self.stage_factors.get(stage, _DEFAULT_STAGE_FACTORS[stage])
        overdue_days = _overdue_days_for_unit(unit, now)
        stale_factor = self.stale_bucket_factors.get(_stale_bucket(overdue_days), 1.0)
        source_factor = self.source_factors.get(int(getattr(unit, "source_id", 0) or 0), 1.0)

        unit_factor = 1.0
        if prior_reviews > 0:
            unit_factor = self.unit_factors.get(int(getattr(unit, "unit_id", 0) or 0), 1.0)

        estimate_seconds = (
            pages
            * self.global_base_minutes_per_page
            * source_factor
            * stage_factor
            * stale_factor
            * unit_factor
            * 60.0
        )
        if estimate_seconds <= 0.0:
            return _static_fallback_seconds(pages, self.fallback_seconds_per_page, self.fallback_seconds_per_unit)
        if self.debug.get("observation_count", 0) <= 0:
            return _static_fallback_seconds(pages, self.fallback_seconds_per_page, self.fallback_seconds_per_unit)
        return max(1.0, float(estimate_seconds))


def build_runtime_estimation_model(
    observations: list[dict],
    fallback_seconds_per_page: float,
    fallback_seconds_per_unit: float,
) -> RuntimeEstimationModel:
    fallback_mpp = max(1.0, float(fallback_seconds_per_page)) / 60.0
    fallback_unit = max(1.0, float(fallback_seconds_per_unit))

    prepared = _prepare_observations(observations)
    if not prepared:
        return RuntimeEstimationModel(
            global_base_minutes_per_page=fallback_mpp,
            source_factors={},
            stage_factors=dict(_DEFAULT_STAGE_FACTORS),
            stale_bucket_factors=dict(_DEFAULT_STALE_FACTORS),
            stale_cap=1.35,
            fallback_seconds_per_page=float(fallback_seconds_per_page),
            fallback_seconds_per_unit=float(fallback_seconds_per_unit),
            debug={"observation_count": 0},
        )

    initial_base = _mean(item["mpp"] for item in prepared)

    stage_raw: dict[str, list[float]] = {k: [] for k in _STAGE_ORDER}
    for item in prepared:
        stage_raw[item["stage"]].append(item["mpp"])
    stage_factors = {
        stage: _shrink_to_default(
            raw_value=(_mean(vals) / max(1e-6, initial_base)) if vals else _DEFAULT_STAGE_FACTORS[stage],
            sample_size=len(vals),
            default_value=_DEFAULT_STAGE_FACTORS[stage],
            strength=10.0,
        )
        for stage, vals in stage_raw.items()
    }

    mature = max(0.01, stage_factors[_STAGE_MATURE])
    stage_factors = {k: v / mature for k, v in stage_factors.items()}

    normalized_stage = []
    for item in prepared:
        sf = max(0.05, stage_factors.get(item["stage"], 1.0))
        norm = item["mpp"] / sf
        copied = dict(item)
        copied["norm_stage_mpp"] = norm
        normalized_stage.append(copied)

    base_after_stage = _mean(x["norm_stage_mpp"] for x in normalized_stage)

    stale_raw: dict[str, list[float]] = {k: [] for k in _STALE_BUCKET_ORDER}
    for item in normalized_stage:
        stale_raw[item["stale_bucket"]].append(item["norm_stage_mpp"])
    stale_factors = {
        bucket: _shrink_to_default(
            raw_value=(_mean(vals) / max(1e-6, base_after_stage)) if vals else _DEFAULT_STALE_FACTORS[bucket],
            sample_size=len(vals),
            default_value=_DEFAULT_STALE_FACTORS[bucket],
            strength=12.0,
        )
        for bucket, vals in stale_raw.items()
    }
    stale_factors = _enforce_monotonic_stale(stale_factors, cap=1.35)

    normalized = []
    for item in normalized_stage:
        stf = max(0.05, stale_factors.get(item["stale_bucket"], 1.0))
        norm = item["norm_stage_mpp"] / stf
        copied = dict(item)
        copied["normalized_mpp"] = norm
        normalized.append(copied)

    global_base = _mean(x["normalized_mpp"] for x in normalized)
    global_base = max(0.01, float(global_base))

    source_values: dict[int, list[float]] = {}
    for item in normalized:
        source_values.setdefault(int(item["source_id"]), []).append(item["normalized_mpp"])
    source_factors = {
        source_id: _clamp(
            _shrink_to_default(
                raw_value=_mean(vals) / global_base,
                sample_size=len(vals),
                default_value=1.0,
                strength=16.0,
            ),
            0.6,
            1.8,
        )
        for source_id, vals in source_values.items()
    }

    unit_values: dict[int, list[float]] = {}
    for item in normalized:
        source_factor = max(0.05, source_factors.get(int(item["source_id"]), 1.0))
        unit_values.setdefault(int(item["unit_id"]), []).append(item["normalized_mpp"] / source_factor)

    unit_factors: dict[int, float] = {}
    for unit_id, vals in unit_values.items():
        if len(vals) < 3:
            continue
        unit_factors[unit_id] = _clamp(
            _shrink_to_default(
                raw_value=_mean(vals) / global_base,
                sample_size=len(vals),
                default_value=1.0,
                strength=8.0,
            ),
            0.7,
            1.5,
        )

    return RuntimeEstimationModel(
        global_base_minutes_per_page=global_base,
        source_factors=source_factors,
        stage_factors=stage_factors,
        stale_bucket_factors=stale_factors,
        stale_cap=1.35,
        fallback_seconds_per_page=float(fallback_seconds_per_page),
        fallback_seconds_per_unit=float(fallback_seconds_per_unit),
        unit_factors=unit_factors,
        debug={
            "observation_count": len(prepared),
            "source_observation_counts": {sid: len(vals) for sid, vals in source_values.items()},
        },
    )


def _prepare_observations(observations: list[dict]) -> list[dict]:
    out: list[dict] = []
    by_unit: dict[int, list[dict]] = {}
    for row in observations:
        by_unit.setdefault(int(row["unit_id"]), []).append(row)

    for unit_id, rows in by_unit.items():
        ordered = sorted(rows, key=lambda r: (str(r["ended_at"]), int(r.get("event_id", 0))))
        history_for_due: list[dict] = []
        for idx, row in enumerate(ordered):
            elapsed = row.get("elapsed_seconds")
            pages = row.get("page_count")
            if elapsed is None or float(elapsed) <= 0 or pages is None or int(pages) <= 0:
                history_for_due.append({"ended_at": row["ended_at"], "rating": row["rating"]})
                continue
            overdue = _overdue_days_from_history(history_for_due, row["ended_at"])
            out.append(
                {
                    "unit_id": unit_id,
                    "source_id": int(row["source_id"]),
                    "mpp": (float(elapsed) / 60.0) / max(1, int(pages)),
                    "stage": _stage_bucket(idx),
                    "stale_bucket": _stale_bucket(overdue),
                }
            )
            history_for_due.append({"ended_at": row["ended_at"], "rating": row["rating"]})
    return out


def _safe_page_count(unit: Any) -> int:
    start = int(getattr(unit, "start_page", 1) or 1)
    end = int(getattr(unit, "end_page", start) or start)
    return max(1, (end - start) + 1)


def _static_fallback_seconds(pages: int, fallback_seconds_per_page: float, fallback_seconds_per_unit: float) -> float:
    return max(1.0, pages * max(1.0, float(fallback_seconds_per_page)), max(1.0, float(fallback_seconds_per_unit)))


def _stage_bucket(prior_reviews: int) -> str:
    if prior_reviews <= 0:
        return _STAGE_NEW
    if prior_reviews == 1:
        return _STAGE_ONE
    if prior_reviews == 2:
        return _STAGE_TWO
    return _STAGE_MATURE


def _stale_bucket(overdue_days: float) -> str:
    if overdue_days < 3.0:
        return _STALE_FRESH
    if overdue_days < 8.0:
        return _STALE_LIGHT
    if overdue_days < 21.0:
        return _STALE_MEDIUM
    return _STALE_HEAVY


def _overdue_days_from_history(history_before_event: list[dict], event_ended_at_iso: str) -> float:
    if not history_before_event:
        return 0.0
    due_iso = _derive_due_at_from_events(history_before_event)
    if not due_iso:
        return 0.0
    try:
        due_at = parse_iso_to_utc(str(due_iso))
        reviewed_at = parse_iso_to_utc(str(event_ended_at_iso))
    except Exception:
        return 0.0
    return max(0.0, (reviewed_at - due_at).total_seconds() / 86400.0)


def _overdue_days_for_unit(unit: Any, now: datetime) -> float:
    due_at_iso = getattr(unit, "next_review_at", None)
    if not due_at_iso:
        return 0.0
    try:
        due_at = parse_iso_to_utc(str(due_at_iso))
    except Exception:
        return 0.0
    return max(0.0, (now - due_at).total_seconds() / 86400.0)


def _shrink_to_default(raw_value: float, sample_size: int, default_value: float, strength: float) -> float:
    n = max(0.0, float(sample_size))
    w = n / (n + max(1.0, float(strength)))
    return (w * float(raw_value)) + ((1.0 - w) * float(default_value))


def _enforce_monotonic_stale(factors: dict[str, float], cap: float) -> dict[str, float]:
    out: dict[str, float] = {}
    running = 1.0
    for bucket in _STALE_BUCKET_ORDER:
        val = max(1.0, min(float(cap), float(factors.get(bucket, 1.0))))
        running = max(running, val)
        out[bucket] = running
    out[_STALE_FRESH] = 1.0
    return out


def _mean(values) -> float:
    vals = [float(v) for v in values]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))

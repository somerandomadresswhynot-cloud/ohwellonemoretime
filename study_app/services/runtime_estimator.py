from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any

from study_app.persistence.repositories import (
    TIME_BUCKET_FIRST_ENCOUNTER,
    TIME_BUCKET_LEARNING_REPEAT,
    TIME_BUCKET_STABILIZING_RECALL,
    TIME_BUCKET_MATURE_REVIEW,
    TIME_BUCKETS,
    classify_event_time_bucket,
)

_BUCKET_FALLBACK = {
    TIME_BUCKET_FIRST_ENCOUNTER: None,
    TIME_BUCKET_LEARNING_REPEAT: TIME_BUCKET_FIRST_ENCOUNTER,
    TIME_BUCKET_STABILIZING_RECALL: TIME_BUCKET_LEARNING_REPEAT,
    TIME_BUCKET_MATURE_REVIEW: TIME_BUCKET_STABILIZING_RECALL,
}


@dataclass(frozen=True)
class RuntimeEstimationModel:
    """Mode-aware projected-time model using robust seconds-per-page medians."""

    global_seconds_per_page_by_bucket: dict[str, float]
    source_seconds_per_page_by_bucket: dict[int, dict[str, float]]
    source_bucket_observation_counts: dict[int, dict[str, int]]
    source_bucket_page_totals: dict[int, dict[str, int]]
    fallback_seconds_per_page: float
    fallback_seconds_per_unit: float
    min_source_observations: int = 3
    min_source_pages: int = 6
    debug: dict[str, Any] = field(default_factory=dict)

    def estimate_unit_seconds_with_details(
        self,
        unit: Any,
        next_session_bucket: str,
        source_id: int | None = None,
    ) -> dict[str, Any]:
        bucket = str(next_session_bucket or TIME_BUCKET_FIRST_ENCOUNTER)
        pages = _safe_page_count(unit)
        src = int(source_id if source_id is not None else (getattr(unit, "source_id", 0) or 0))
        spp, provenance = self._estimate_seconds_per_page(bucket, src)
        projected = max(1.0, float(spp) * float(pages), float(self.fallback_seconds_per_unit))
        return {
            "bucket": bucket,
            "page_count": pages,
            "seconds_per_page": float(spp),
            "seconds": float(projected),
            "provenance": provenance,
        }

    def estimate_unit_seconds(self, unit: Any, next_session_bucket: str, source_id: int | None = None) -> float:
        details = self.estimate_unit_seconds_with_details(unit, next_session_bucket, source_id=source_id)
        return float(details["seconds"])

    def _estimate_seconds_per_page(self, bucket: str, source_id: int) -> tuple[float, str]:
        chain = _fallback_chain(bucket)
        source_stats = self.source_seconds_per_page_by_bucket.get(int(source_id), {})
        source_counts = self.source_bucket_observation_counts.get(int(source_id), {})
        source_pages = self.source_bucket_page_totals.get(int(source_id), {})

        for candidate in chain:
            source_value = source_stats.get(candidate)
            if source_value is not None:
                if int(source_counts.get(candidate, 0)) >= self.min_source_observations and int(source_pages.get(candidate, 0)) >= self.min_source_pages:
                    return float(source_value), f"source:{candidate}"
            global_value = self.global_seconds_per_page_by_bucket.get(candidate)
            if global_value is not None:
                return float(global_value), f"global:{candidate}"

        defaults = _default_bucket_seconds_per_page(self.fallback_seconds_per_page)
        return float(defaults.get(bucket, self.fallback_seconds_per_page)), "default"


def build_runtime_estimation_model(
    observations: list[dict],
    fallback_seconds_per_page: float,
    fallback_seconds_per_unit: float,
    min_source_observations: int = 3,
    min_source_pages: int = 6,
) -> RuntimeEstimationModel:
    prepared = _prepare_observations(observations)

    values_global: dict[str, list[float]] = {bucket: [] for bucket in TIME_BUCKETS}
    values_source: dict[int, dict[str, list[float]]] = {}
    counts_source: dict[int, dict[str, int]] = {}
    pages_source: dict[int, dict[str, int]] = {}

    for row in prepared:
        bucket = str(row["bucket"])
        source_id = int(row["source_id"])
        spp = float(row["seconds_per_page"])
        pages = int(row["page_count"])

        values_global[bucket].append(spp)
        values_source.setdefault(source_id, {b: [] for b in TIME_BUCKETS})[bucket].append(spp)
        counts_source.setdefault(source_id, {b: 0 for b in TIME_BUCKETS})[bucket] += 1
        pages_source.setdefault(source_id, {b: 0 for b in TIME_BUCKETS})[bucket] += pages

    global_medians = {
        bucket: _median_or_none(values_global.get(bucket, []))
        for bucket in TIME_BUCKETS
    }
    source_medians = {
        source_id: {
            bucket: _median_or_none(bucket_vals.get(bucket, []))
            for bucket in TIME_BUCKETS
        }
        for source_id, bucket_vals in values_source.items()
    }

    cleaned_global = {k: float(v) for k, v in global_medians.items() if v is not None}
    cleaned_source: dict[int, dict[str, float]] = {}
    for source_id, bucket_map in source_medians.items():
        cleaned = {k: float(v) for k, v in bucket_map.items() if v is not None}
        if cleaned:
            cleaned_source[int(source_id)] = cleaned

    return RuntimeEstimationModel(
        global_seconds_per_page_by_bucket=cleaned_global,
        source_seconds_per_page_by_bucket=cleaned_source,
        source_bucket_observation_counts=counts_source,
        source_bucket_page_totals=pages_source,
        fallback_seconds_per_page=max(1.0, float(fallback_seconds_per_page)),
        fallback_seconds_per_unit=max(1.0, float(fallback_seconds_per_unit)),
        min_source_observations=max(1, int(min_source_observations)),
        min_source_pages=max(1, int(min_source_pages)),
        debug={"observation_count": len(prepared)},
    )


def _prepare_observations(observations: list[dict]) -> list[dict]:
    out: list[dict] = []
    by_unit: dict[int, list[dict]] = {}
    for row in observations:
        by_unit.setdefault(int(row["unit_id"]), []).append(row)

    for _unit_id, rows in by_unit.items():
        ordered = sorted(rows, key=lambda r: (str(r.get("ended_at") or ""), int(r.get("event_id", 0))))
        prior_events: list[dict] = []
        for row in ordered:
            bucket = classify_event_time_bucket(row, prior_events)
            prior_events.append(row)
            if bucket not in TIME_BUCKETS:
                continue

            elapsed = row.get("elapsed_seconds")
            pages = row.get("page_count")
            if elapsed is None or pages is None:
                continue
            elapsed_seconds = float(elapsed)
            page_count = max(1, int(pages))
            if elapsed_seconds < 5.0:
                continue
            if elapsed_seconds > 7200.0:
                continue
            seconds_per_page = elapsed_seconds / float(page_count)
            if seconds_per_page <= 0.0 or seconds_per_page > 3600.0:
                continue
            out.append(
                {
                    "source_id": int(row["source_id"]),
                    "bucket": str(bucket),
                    "seconds_per_page": float(seconds_per_page),
                    "page_count": page_count,
                }
            )
    return out


def _safe_page_count(unit: Any) -> int:
    start = int(getattr(unit, "start_page", 1) or 1)
    end = int(getattr(unit, "end_page", start) or start)
    return max(1, (end - start) + 1)


def _median_or_none(values: list[float]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return None
    return float(median(vals))


def _default_bucket_seconds_per_page(base_seconds_per_page: float) -> dict[str, float]:
    base = max(1.0, float(base_seconds_per_page))
    return {
        TIME_BUCKET_FIRST_ENCOUNTER: base * 1.35,
        TIME_BUCKET_LEARNING_REPEAT: base * 1.15,
        TIME_BUCKET_STABILIZING_RECALL: base,
        TIME_BUCKET_MATURE_REVIEW: base * 0.85,
    }


def _fallback_chain(bucket: str) -> list[str]:
    out: list[str] = []
    cursor: str | None = str(bucket or TIME_BUCKET_FIRST_ENCOUNTER)
    seen: set[str] = set()
    while cursor and cursor not in seen:
        seen.add(cursor)
        out.append(cursor)
        cursor = _BUCKET_FALLBACK.get(cursor)
    return out or [TIME_BUCKET_FIRST_ENCOUNTER]

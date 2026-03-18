from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


TUnit = TypeVar("TUnit")


@dataclass
class QueueSuggestion(Generic[TUnit]):
    unit: TUnit
    reason: str
    estimated_minutes: float


@dataclass
class QueuePlan(Generic[TUnit]):
    selected_units: list[TUnit]
    overflow_count: int
    projected_minutes: float
    suggested_units: list[QueueSuggestion[TUnit]]


def plan_session_queue(
    due_units: list[TUnit],
    available_minutes: int,
    estimate_seconds: Callable[[TUnit], float],
    strict_progression_sources: set[int] | None = None,
    source_id_of: Callable[[TUnit], int] | None = None,
) -> QueuePlan[TUnit]:
    """Fit due units into a time budget while preserving due order.

    - `due_units` should already be ordered by scheduling priority.
    - `estimate_seconds(unit)` returns estimated time cost for each unit.
    - `available_minutes` is the session budget in minutes.
    - `strict_progression_sources` indicates source ids that should get
      progression-critical out-of-budget suggestions.
    """
    strict_progression_sources = strict_progression_sources or set()
    max_seconds = max(0.0, float(available_minutes) * 60.0)
    selected: list[TUnit] = []
    overflow: list[TUnit] = []
    used_seconds = 0.0

    for unit in due_units:
        estimated = max(1.0, float(estimate_seconds(unit)))
        if used_seconds + estimated <= max_seconds:
            selected.append(unit)
            used_seconds += estimated
        else:
            overflow.append(unit)

    suggestions: list[QueueSuggestion[TUnit]] = []
    if overflow:
        candidate = overflow[0]
        candidate_reason = "out_of_budget_needed_for_progression"
        if strict_progression_sources and source_id_of is not None:
            if source_id_of(candidate) in strict_progression_sources:
                candidate_reason = "strict_order_progression_gate"
        suggestions.append(
            QueueSuggestion(
                unit=candidate,
                reason=candidate_reason,
                estimated_minutes=max(1.0, float(estimate_seconds(candidate))) / 60.0,
            )
        )

    if strict_progression_sources and source_id_of is not None:
        selected_ids = {id(u) for u in selected}
        for source_id in strict_progression_sources:
            due_for_source = [u for u in due_units if source_id_of(u) == source_id]
            if not due_for_source:
                continue
            first_due = due_for_source[0]
            if id(first_due) in selected_ids:
                continue
            if any(id(s.unit) == id(first_due) for s in suggestions):
                continue
            suggestions.append(
                QueueSuggestion(
                    unit=first_due,
                    reason="strict_order_progression_gate",
                    estimated_minutes=max(1.0, float(estimate_seconds(first_due))) / 60.0,
                )
            )

    overflow_count = max(0, len(due_units) - len(selected))
    projected_minutes = used_seconds / 60.0
    return QueuePlan(
        selected_units=selected,
        overflow_count=overflow_count,
        projected_minutes=projected_minutes,
        suggested_units=suggestions,
    )

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar


TUnit = TypeVar("TUnit")


@dataclass
class QueuePlan(Generic[TUnit]):
    selected_units: list[TUnit]
    overflow_count: int
    projected_minutes: float


def plan_session_queue(
    due_units: list[TUnit],
    available_minutes: int,
    estimate_seconds: Callable[[TUnit], float],
) -> QueuePlan[TUnit]:
    """Fit due units into a time budget while preserving due order.

    - `due_units` should already be ordered by scheduling priority.
    - `estimate_seconds(unit)` returns estimated time cost for each unit.
    - `available_minutes` is the session budget in minutes.
    """
    max_seconds = max(0.0, float(available_minutes) * 60.0)
    selected: list[TUnit] = []
    used_seconds = 0.0

    for unit in due_units:
        estimated = max(1.0, float(estimate_seconds(unit)))
        if used_seconds + estimated <= max_seconds:
            selected.append(unit)
            used_seconds += estimated
        else:
            break

    overflow_count = max(0, len(due_units) - len(selected))
    projected_minutes = used_seconds / 60.0
    return QueuePlan(
        selected_units=selected,
        overflow_count=overflow_count,
        projected_minutes=projected_minutes,
    )


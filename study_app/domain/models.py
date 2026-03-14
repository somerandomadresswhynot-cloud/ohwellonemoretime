from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Source:
    id: int
    title: str
    file_path: str
    file_exists: bool
    file_size: int
    page_count: int
    is_active: bool
    created_at: str
    updated_at: str


@dataclass
class OutlineNode:
    id: int
    source_id: int
    parent_id: Optional[int]
    title: str
    depth: int
    order_index: int
    start_page: Optional[int]
    end_page: Optional[int]
    is_unit: bool
    queue_enabled: bool


@dataclass
class UnitView:
    unit_id: int
    source_id: int
    source_title: str
    title: str
    start_page: int
    end_page: int
    queue_enabled: bool
    next_review_at: Optional[str]
    last_review_at: Optional[str]
    review_count: int
    avg_rating: float


@dataclass
class ReviewEvent:
    id: int
    unit_id: int
    started_at: str
    ended_at: str
    elapsed_seconds: int
    rating: str
    pre_note: str
    post_note: str
    interval_days: float
    next_review_at: str


RATING_TO_SCORE = {
    "easy": 5,
    "with_effort": 3,
    "hard": 2,
    "skip": 0,
}


def utcnow_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

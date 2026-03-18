from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional


NodeType = Literal["container", "unit"]


@dataclass
class Source:
    id: int
    title: str
    file_path: str
    file_exists: bool
    file_size: int
    page_count: int
    is_active: bool
    learning_mode: str
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

    @property
    def node_type(self) -> NodeType:
        return "unit" if self.is_unit else "container"


@dataclass
class UnitView:
    unit_id: int
    node_id: int
    source_id: int
    source_title: str
    title: str
    hierarchy_path: str
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


@dataclass
class HighlightRecord:
    id: int
    source_id: int
    unit_id: Optional[int]
    page: int
    page_index: int
    quote_text: str
    anchor_type: str
    text_prefix: str
    text_exact: str
    text_suffix: str
    rects_json: str
    opacity: float
    label: str
    note: str
    color: str
    created_at: str
    updated_at: str


RATING_TO_SCORE = {
    "easy": 5,
    "with_effort": 3,
    "hard": 2,
    "skip": 0,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat(timespec="seconds")


def parse_iso_to_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utcnow_iso() -> str:
    return iso_utc(now_utc())

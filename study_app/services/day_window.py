from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from study_app.domain.models import iso_utc, parse_iso_to_utc

_GMT_OFFSET_RE = re.compile(r"^[+-](?:0\d|1\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class DayWindow:
    offset: str
    local_day_start: datetime
    local_next_day_start: datetime
    utc_start: datetime
    utc_next_start: datetime

    @property
    def utc_start_iso(self) -> str:
        return iso_utc(self.utc_start)

    @property
    def utc_next_start_iso(self) -> str:
        return iso_utc(self.utc_next_start)

    @property
    def day_key(self) -> str:
        return self.local_day_start.date().isoformat()


def is_valid_gmt_offset(offset: str) -> bool:
    return bool(_GMT_OFFSET_RE.match(str(offset or "")))


def parse_gmt_offset(offset: str | None) -> timezone:
    value = (offset or "+00:00").strip()
    if not is_valid_gmt_offset(value):
        raise ValueError("Invalid GMT offset format. Expected ±HH:MM")
    sign = 1 if value[0] == "+" else -1
    hours = int(value[1:3])
    minutes = int(value[4:6])
    total = sign * ((hours * 60) + minutes)
    return timezone(timedelta(minutes=total), name=f"GMT{value}")


def normalized_gmt_offset(offset: str | None, default: str = "+00:00") -> str:
    candidate = (offset or "").strip() or default
    if not is_valid_gmt_offset(candidate):
        return default
    return candidate


def day_window_for_offset(offset: str | None, now_utc: datetime | str | None = None) -> DayWindow:
    tz = parse_gmt_offset(normalized_gmt_offset(offset))
    if now_utc is None:
        now_utc_dt = datetime.now(timezone.utc)
    elif isinstance(now_utc, str):
        now_utc_dt = parse_iso_to_utc(now_utc)
    else:
        now_utc_dt = now_utc
    now_utc_dt = now_utc_dt.astimezone(timezone.utc)
    now_local = now_utc_dt.astimezone(tz)
    local_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    local_next = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_next = local_next.astimezone(timezone.utc)
    return DayWindow(
        offset=normalized_gmt_offset(offset),
        local_day_start=local_start,
        local_next_day_start=local_next,
        utc_start=utc_start,
        utc_next_start=utc_next,
    )

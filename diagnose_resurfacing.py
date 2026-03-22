#!/usr/bin/env python3
"""Diagnose why previously reviewed units are not showing in today's queue.

Usage:
  python diagnose_resurfacing.py [db_path]

Default db_path: ./study_app.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_timedelta_days(days: float) -> str:
    if days < 1:
        return f"{days*24:.1f}h"
    return f"{days:.1f}d"


def main() -> int:
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("study_app.db")
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    snapshot_raw = conn.execute(
        "SELECT value FROM app_settings WHERE key='daily_queue_snapshot_json'"
    ).fetchone()
    snapshot = {}
    if snapshot_raw and snapshot_raw["value"]:
        try:
            snapshot = json.loads(snapshot_raw["value"])
        except Exception:
            snapshot = {}
    snapshot_date = str(snapshot.get("date") or "")
    snapshot_ids = {int(x) for x in snapshot.get("unit_ids", []) if str(x).isdigit()}

    rows = conn.execute(
        """
        SELECT
            u.id,
            u.source_id,
            s.title AS source_title,
            s.is_active,
            u.title,
            u.queue_enabled,
            u.last_review_at,
            u.next_review_at,
            u.review_count,
            u.fsrs_review_count,
            (
              SELECT MAX(re.ended_at)
              FROM review_events re
              WHERE re.unit_id=u.id AND re.deleted_at IS NULL
            ) AS last_event_at
        FROM units u
        JOIN sources s ON s.id=u.source_id
        WHERE (u.review_count > 0 OR COALESCE(u.fsrs_review_count,0) > 0 OR u.last_review_at IS NOT NULL)
        ORDER BY COALESCE(u.next_review_at,''), s.title, u.title
        """
    ).fetchall()

    print(f"Now (UTC): {now_iso}")
    print(f"Snapshot date: {snapshot_date or 'none'} (today: {today})")
    print(f"Snapshot units: {len(snapshot_ids)}")
    print(f"Previously-reviewed units considered: {len(rows)}")
    print()

    excluded = []
    not_due = []
    due_missing_snapshot = []
    due_in_snapshot = []

    for r in rows:
        next_dt = parse_iso(r["next_review_at"])
        due_now = next_dt is None or next_dt <= now
        in_snapshot = int(r["id"]) in snapshot_ids
        queue_on = bool(r["queue_enabled"])
        source_on = bool(r["is_active"])

        item = {
            "id": int(r["id"]),
            "source": str(r["source_title"]),
            "title": str(r["title"]),
            "queue_enabled": queue_on,
            "source_active": source_on,
            "next_review_at": str(r["next_review_at"] or ""),
            "in_snapshot": in_snapshot,
        }

        if not queue_on or not source_on:
            excluded.append(item)
            continue

        if due_now:
            if snapshot_date == today and snapshot_ids and not in_snapshot:
                due_missing_snapshot.append(item)
            else:
                due_in_snapshot.append(item)
        else:
            delta_days = (next_dt - now).total_seconds() / 86400.0 if next_dt else 0.0
            item["due_in"] = fmt_timedelta_days(delta_days)
            not_due.append(item)

    def print_items(header: str, items: list[dict], max_rows: int = 30) -> None:
        print(f"{header}: {len(items)}")
        for i, item in enumerate(items[:max_rows], 1):
            extra = ""
            if "due_in" in item:
                extra += f" | due_in={item['due_in']}"
            extra += f" | next={item['next_review_at'] or 'NULL'} | q={int(item['queue_enabled'])} src={int(item['source_active'])}"
            extra += f" | in_snapshot={item['in_snapshot']}"
            print(f"  {i:02d}. [u#{item['id']}] {item['source']} :: {item['title']}{extra}")
        if len(items) > max_rows:
            print(f"  ... {len(items)-max_rows} more")
        print()

    print_items("Excluded by source/queue flags", excluded)
    print_items("Not due yet", not_due)
    print_items("Due but missing from today's snapshot", due_missing_snapshot)
    print_items("Due and available for queue", due_in_snapshot)

    if due_missing_snapshot:
        print("Hint: today's snapshot is fixed; rebuild/clear it if you want newly-due items to appear immediately.")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

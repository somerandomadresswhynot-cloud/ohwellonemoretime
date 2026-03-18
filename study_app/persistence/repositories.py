from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from study_app.domain.models import Source, UnitView, utcnow_iso
from study_app.persistence.database import Database


class SourceRepo:
    def __init__(self, db: Database):
        self.db = db

    def list_sources(self, q: str = "") -> list[Source]:
        sql = "SELECT * FROM sources WHERE 1=1"
        params = []
        if q:
            sql += " AND lower(title) LIKE ?"
            params.append(f"%{q.lower()}%")
        sql += " ORDER BY updated_at DESC"
        rows = self.db.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            fp = Path(r["file_path"])
            out.append(Source(
                id=r["id"], title=r["title"], file_path=r["file_path"], file_exists=fp.exists(),
                file_size=r["file_size"], page_count=r["page_count"], is_active=bool(r["is_active"]),
                learning_mode=r["learning_mode"] or "any", created_at=r["created_at"], updated_at=r["updated_at"]
            ))
        return out

    def get(self, source_id: int) -> Optional[Source]:
        row = self.db.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            return None
        fp = Path(row["file_path"])
        return Source(
            id=row["id"], title=row["title"], file_path=row["file_path"], file_exists=fp.exists(),
            file_size=row["file_size"], page_count=row["page_count"], is_active=bool(row["is_active"]),
            learning_mode=row["learning_mode"] or "any", created_at=row["created_at"], updated_at=row["updated_at"]
        )

    def create(self, title: str, file_path: str, file_size: int, page_count: int, learning_mode: str = "any") -> int:
        now = utcnow_iso()
        cur = self.db.conn.execute(
            "INSERT INTO sources(title,file_path,file_size,page_count,learning_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (title, file_path, file_size, page_count, learning_mode, now, now),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def update_metadata(self, source_id: int, title: str, is_active: bool, learning_mode: str = "any") -> None:
        self.db.conn.execute(
            "UPDATE sources SET title=?, is_active=?, learning_mode=?, updated_at=? WHERE id=?",
            (title, int(is_active), learning_mode, utcnow_iso(), source_id),
        )
        self.db.conn.commit()

    def relink(self, source_id: int, file_path: str, file_size: int, page_count: int) -> None:
        self.db.conn.execute(
            "UPDATE sources SET file_path=?, file_size=?, page_count=?, updated_at=? WHERE id=?",
            (file_path, file_size, page_count, utcnow_iso(), source_id),
        )
        self.db.conn.commit()

    def delete(self, source_id: int) -> None:
        self.db.conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
        self.db.conn.commit()


class OutlineRepo:
    def __init__(self, db: Database):
        self.db = db

    def nodes_for_source(self, source_id: int):
        return self.db.conn.execute(
            "SELECT * FROM outline_nodes WHERE source_id=? ORDER BY order_index ASC", (source_id,)
        ).fetchall()

    def replace_outline(self, source_id: int, entries: list[dict]) -> None:
        self.db.conn.execute("DELETE FROM outline_nodes WHERE source_id=?", (source_id,))
        parent_stack: dict[int, int] = {}
        id_map = {}
        for idx, e in enumerate(entries):
            depth = e["depth"]
            parent_id = parent_stack.get(depth - 1)
            cur = self.db.conn.execute(
                """INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (source_id, parent_id, e["title"], depth, idx, e.get("start_page"), e.get("end_page"), int(e["is_unit"]), int(e.get("queue_enabled", True))),
            )
            node_id = int(cur.lastrowid)
            id_map[idx] = node_id
            parent_stack[depth] = node_id
            for d in list(parent_stack.keys()):
                if d > depth:
                    parent_stack.pop(d, None)
        self.db.conn.execute("DELETE FROM units WHERE source_id=?", (source_id,))
        rows = self.db.conn.execute("SELECT * FROM outline_nodes WHERE source_id=? AND is_unit=1", (source_id,)).fetchall()
        for r in rows:
            sp = r["start_page"] or 1
            ep = r["end_page"] or sp
            self.db.conn.execute(
                "INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,next_review_at) VALUES(?,?,?,?,?,?,?)",
                (source_id, r["id"], r["title"], sp, ep, r["queue_enabled"], utcnow_iso()),
            )
        self.db.conn.commit()

    def set_queue_enabled(self, node_id: int, enabled: bool) -> None:
        self.db.conn.execute(
            """WITH RECURSIVE subtree(id) AS (
                SELECT id FROM outline_nodes WHERE id=?
                UNION ALL
                SELECT n.id FROM outline_nodes n
                JOIN subtree s ON n.parent_id=s.id
            )
            UPDATE outline_nodes SET queue_enabled=? WHERE id IN (SELECT id FROM subtree)
            """,
            (node_id, int(enabled)),
        )
        self.db.conn.execute(
            """WITH RECURSIVE subtree(id) AS (
                SELECT id FROM outline_nodes WHERE id=?
                UNION ALL
                SELECT n.id FROM outline_nodes n
                JOIN subtree s ON n.parent_id=s.id
            )
            UPDATE units SET queue_enabled=? WHERE node_id IN (SELECT id FROM subtree)
            """,
            (node_id, int(enabled)),
        )
        self.db.conn.commit()

    def bulk_set_source(self, source_id: int, enabled: bool) -> None:
        self.db.conn.execute("UPDATE outline_nodes SET queue_enabled=? WHERE source_id=?", (int(enabled), source_id))
        self.db.conn.execute("UPDATE units SET queue_enabled=? WHERE source_id=?", (int(enabled), source_id))
        self.db.conn.commit()


class ReviewRepo:
    def __init__(self, db: Database):
        self.db = db

    def due_units(self, now_iso: str) -> list[UnitView]:
        rows = self.db.conn.execute(
            """WITH RECURSIVE node_path(node_id, path) AS (
                SELECT n.id, n.title
                FROM outline_nodes n
                WHERE n.parent_id IS NULL
                UNION ALL
                SELECT c.id, node_path.path || ' › ' || c.title
                FROM outline_nodes c
                JOIN node_path ON c.parent_id=node_path.node_id
            )
            SELECT u.*, s.title AS source_title, COALESCE(node_path.path, u.title) AS hierarchy_path
            FROM units u
            JOIN sources s ON s.id=u.source_id
            LEFT JOIN node_path ON node_path.node_id=u.node_id
            WHERE s.is_active=1 AND u.queue_enabled=1 AND (u.next_review_at IS NULL OR u.next_review_at<=?)
            ORDER BY COALESCE(u.next_review_at,'') ASC""",
            (now_iso,),
        ).fetchall()
        return [UnitView(
            unit_id=r["id"], node_id=r["node_id"], source_id=r["source_id"], source_title=r["source_title"],
            title=r["title"], hierarchy_path=r["hierarchy_path"],
            start_page=r["start_page"], end_page=r["end_page"], queue_enabled=bool(r["queue_enabled"]),
            next_review_at=r["next_review_at"], last_review_at=r["last_review_at"], review_count=r["review_count"], avg_rating=r["avg_rating"]
        ) for r in rows]

    def avg_elapsed_seconds_for_unit(self, unit_id: int) -> float | None:
        row = self.db.conn.execute(
            """SELECT AVG(elapsed_seconds) AS avg_elapsed
            FROM review_events
            WHERE unit_id=? AND deleted_at IS NULL""",
            (unit_id,),
        ).fetchone()
        if not row or row["avg_elapsed"] is None:
            return None
        return float(row["avg_elapsed"])

    def avg_elapsed_seconds_for_source(self, source_id: int) -> float | None:
        row = self.db.conn.execute(
            """SELECT AVG(re.elapsed_seconds) AS avg_elapsed
            FROM review_events re
            JOIN units u ON u.id=re.unit_id
            WHERE u.source_id=? AND re.deleted_at IS NULL""",
            (source_id,),
        ).fetchone()
        if not row or row["avg_elapsed"] is None:
            return None
        return float(row["avg_elapsed"])

    def avg_elapsed_seconds_global(self) -> float | None:
        row = self.db.conn.execute(
            """SELECT AVG(elapsed_seconds) AS avg_elapsed
            FROM review_events
            WHERE deleted_at IS NULL"""
        ).fetchone()
        if not row or row["avg_elapsed"] is None:
            return None
        return float(row["avg_elapsed"])

    def source_units(self, source_id: int):
        return self.db.conn.execute("SELECT * FROM units WHERE source_id=? ORDER BY start_page,title", (source_id,)).fetchall()

    def events_for_unit(self, unit_id: int):
        return self.db.conn.execute(
            "SELECT * FROM review_events WHERE unit_id=? AND deleted_at IS NULL ORDER BY ended_at DESC", (unit_id,)
        ).fetchall()

    def add_event(self, unit_id: int, payload: dict) -> int:
        cur = self.db.conn.execute(
            """INSERT INTO review_events(unit_id,started_at,ended_at,elapsed_seconds,rating,pre_note,post_note,interval_days,next_review_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (unit_id, payload["started_at"], payload["ended_at"], payload["elapsed_seconds"], payload["rating"], payload["pre_note"], payload["post_note"], payload["interval_days"], payload["next_review_at"]),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def record_review(self, unit_id: int, payload: dict, unit_stats: dict) -> int:
        with self.db.conn:
            cur = self.db.conn.execute(
                """INSERT INTO review_events(unit_id,started_at,ended_at,elapsed_seconds,rating,pre_note,post_note,interval_days,next_review_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    unit_id,
                    payload["started_at"],
                    payload["ended_at"],
                    payload["elapsed_seconds"],
                    payload["rating"],
                    payload["pre_note"],
                    payload["post_note"],
                    payload["interval_days"],
                    payload["next_review_at"],
                ),
            )
            update_cur = self.db.conn.execute(
                """UPDATE units
                SET last_review_at=?,next_review_at=?,review_count=?,ease_factor=?,interval_days=?,avg_rating=?
                WHERE id=?""",
                (
                    unit_stats["last_review_at"],
                    unit_stats["next_review_at"],
                    unit_stats["review_count"],
                    unit_stats["ease_factor"],
                    unit_stats["interval_days"],
                    unit_stats["avg_rating"],
                    unit_id,
                ),
            )
            if update_cur.rowcount != 1:
                raise RuntimeError(f"Expected to update exactly one unit row for unit_id={unit_id}")
            return int(cur.lastrowid)

    def update_unit_stats(self, unit_id: int, data: dict) -> None:
        self.db.conn.execute(
            """UPDATE units SET last_review_at=?,next_review_at=?,review_count=?,ease_factor=?,interval_days=?,avg_rating=? WHERE id=?""",
            (data["last_review_at"], data["next_review_at"], data["review_count"], data["ease_factor"], data["interval_days"], data["avg_rating"], unit_id),
        )
        self.db.conn.commit()

    def unit_by_id(self, unit_id: int):
        return self.db.conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()

    def edit_event_notes(self, event_id: int, pre_note: str, post_note: str) -> None:
        before = self.db.conn.execute("SELECT pre_note,post_note FROM review_events WHERE id=?", (event_id,)).fetchone()
        self.db.conn.execute("UPDATE review_events SET pre_note=?, post_note=? WHERE id=?", (pre_note, post_note, event_id))
        self.db.conn.execute(
            "INSERT INTO review_revisions(review_event_id,changed_at,action,before_json,after_json) VALUES(?,?,?,?,?)",
            (event_id, utcnow_iso(), "edit_notes", json.dumps(dict(before)), json.dumps({"pre_note": pre_note, "post_note": post_note})),
        )
        self.db.conn.commit()

    def soft_delete_event(self, event_id: int) -> None:
        before = self.db.conn.execute("SELECT * FROM review_events WHERE id=?", (event_id,)).fetchone()
        self.db.conn.execute("UPDATE review_events SET deleted_at=? WHERE id=?", (utcnow_iso(), event_id))
        self.db.conn.execute(
            "INSERT INTO review_revisions(review_event_id,changed_at,action,before_json,after_json) VALUES(?,?,?,?,?)",
            (event_id, utcnow_iso(), "delete", json.dumps(dict(before)), json.dumps({"deleted": True})),
        )
        self.db.conn.commit()


class SettingsRepo:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, default: str) -> str:
        row = self.db.conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.db.conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.conn.commit()

    def get_ui_state(self, key: str, default: str = "") -> str:
        row = self.db.conn.execute("SELECT value FROM ui_state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_ui_state(self, key: str, value: str) -> None:
        self.db.conn.execute(
            "INSERT INTO ui_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.conn.commit()


class HighlightRepo:
    def __init__(self, db: Database):
        self.db = db

    def add_highlight(self, source_id: int, page: int, quote_text: str, note: str = "", color: str = "#2d9cdb") -> int:
        return self.add_text_highlight(
            source_id=source_id,
            page=page,
            quote_text=quote_text,
            note=note,
            color=color,
        )

    def add_text_highlight(
        self,
        source_id: int,
        page: int,
        quote_text: str,
        note: str = "",
        color: str = "#2d9cdb",
        text_prefix: str = "",
        text_suffix: str = "",
        opacity: float = 0.35,
        label: str = "",
    ) -> int:
        unit_row = self.db.conn.execute(
            """SELECT u.id AS unit_id, n.depth AS depth, (u.end_page-u.start_page) AS span
            FROM units u JOIN outline_nodes n ON n.id=u.node_id
            WHERE u.source_id=? AND u.start_page<=? AND u.end_page>=?
            ORDER BY n.depth DESC, span ASC
            LIMIT 1""",
            (source_id, page, page),
        ).fetchone()
        unit_id = unit_row["unit_id"] if unit_row else None
        cur = self.db.conn.execute(
            """INSERT INTO highlights(
                source_id,unit_id,page,page_index,quote_text,anchor_type,text_prefix,text_exact,text_suffix,rects_json,opacity,label,note,color,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_id,
                unit_id,
                page,
                max(0, page - 1),
                quote_text,
                "text",
                text_prefix,
                quote_text,
                text_suffix,
                "[]",
                float(opacity),
                label,
                note,
                color,
                utcnow_iso(),
                utcnow_iso(),
            ),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def add_area_highlight(
        self,
        source_id: int,
        page: int,
        rects: list[dict],
        note: str = "",
        color: str = "#2d9cdb",
        quote_text: str = "",
        opacity: float = 0.35,
        label: str = "",
    ) -> int:
        unit_row = self.db.conn.execute(
            """SELECT u.id AS unit_id, n.depth AS depth, (u.end_page-u.start_page) AS span
            FROM units u JOIN outline_nodes n ON n.id=u.node_id
            WHERE u.source_id=? AND u.start_page<=? AND u.end_page>=?
            ORDER BY n.depth DESC, span ASC
            LIMIT 1""",
            (source_id, page, page),
        ).fetchone()
        unit_id = unit_row["unit_id"] if unit_row else None
        rects_json = json.dumps(rects, separators=(",", ":"))
        cur = self.db.conn.execute(
            """INSERT INTO highlights(
                source_id,unit_id,page,page_index,quote_text,anchor_type,text_prefix,text_exact,text_suffix,rects_json,opacity,label,note,color,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source_id,
                unit_id,
                page,
                max(0, page - 1),
                quote_text,
                "rect",
                "",
                quote_text,
                "",
                rects_json,
                float(opacity),
                label,
                note,
                color,
                utcnow_iso(),
                utcnow_iso(),
            ),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def find_exact(self, source_id: int, page: int, quote_text: str):
        return self.db.conn.execute(
            """SELECT * FROM highlights
            WHERE source_id=? AND page=? AND quote_text=?
            ORDER BY created_at DESC LIMIT 1""",
            (source_id, page, quote_text),
        ).fetchone()

    def delete_highlight(self, highlight_id: int) -> None:
        self.db.conn.execute("DELETE FROM highlights WHERE id=?", (highlight_id,))
        self.db.conn.commit()

    def update_highlight_note(self, highlight_id: int, note: str) -> None:
        self.db.conn.execute("UPDATE highlights SET note=?, updated_at=? WHERE id=?", (note, utcnow_iso(), highlight_id))
        self.db.conn.commit()

    def update_highlight_color(self, highlight_id: int, color: str) -> None:
        self.db.conn.execute("UPDATE highlights SET color=?, updated_at=? WHERE id=?", (color, utcnow_iso(), highlight_id))
        self.db.conn.commit()

    def list_unit_highlights(self, unit_id: int):
        return self.db.conn.execute(
            "SELECT * FROM highlights WHERE unit_id=? ORDER BY created_at DESC", (unit_id,)
        ).fetchall()

    def list_source_highlights(self, source_id: int):
        return self.db.conn.execute(
            """SELECT h.*, u.title AS unit_title, u.start_page, u.end_page
            FROM highlights h
            LEFT JOIN units u ON u.id=h.unit_id
            WHERE h.source_id=?
            ORDER BY h.page ASC, h.created_at DESC""",
            (source_id,),
        ).fetchall()

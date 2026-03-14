from __future__ import annotations

import re
from dataclasses import dataclass

LINE_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>[^\[]+?)(?:\s*\[p(?P<start>\d+)(?:-(?P<end>\d+))?\])?\s*$")


@dataclass
class ParseError:
    line_no: int
    message: str


def entries_to_text(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        hashes = "#" * e["depth"]
        page = ""
        if e.get("start_page"):
            ep = e.get("end_page") or e["start_page"]
            page = f" [p{e['start_page']}-{ep}]"
        lines.append(f"{hashes} {e['title']}{page}")
    return "\n".join(lines)


def parse_outline_text(text: str) -> tuple[list[dict], list[ParseError]]:
    entries = []
    errors = []
    for i, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        m = LINE_RE.match(raw)
        if not m:
            errors.append(ParseError(i, "Expected: ## Title [p1-3]"))
            continue
        depth = len(m.group("hashes"))
        title = m.group("title").strip()
        sp = int(m.group("start")) if m.group("start") else None
        ep = int(m.group("end")) if m.group("end") else sp
        entries.append({
            "depth": depth,
            "title": title,
            "start_page": sp,
            "end_page": ep,
            "is_unit": bool(sp),
            "queue_enabled": True,
        })
    return entries, errors


def seed_outline_from_pages(page_count: int, title: str) -> list[dict]:
    out = [{"depth": 1, "title": title, "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True}]
    chunk = 5
    for i in range(1, page_count + 1, chunk):
        end = min(page_count, i + chunk - 1)
        out.append({"depth": 2, "title": f"Pages {i}-{end}", "start_page": i, "end_page": end, "is_unit": True, "queue_enabled": True})
    return out

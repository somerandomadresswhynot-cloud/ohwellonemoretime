from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from study_app.services.outline_service import entries_from_bookmarks, entries_from_headings, seed_outline_from_pages


class PdfService:
    def inspect(self, path: str) -> tuple[int, int]:
        p = Path(path)
        reader = PdfReader(path)
        return p.stat().st_size, len(reader.pages)

    def generate_outline_entries(self, path: str, source_title: str, page_count: int) -> list[dict]:
        reader = PdfReader(path)
        bookmarks = self._extract_bookmarks(reader)
        if len(bookmarks) >= 3:
            return entries_from_bookmarks(bookmarks, page_count, source_title)

        headings = self._infer_headings(reader)
        if len(headings) >= 3:
            return entries_from_headings(headings, page_count, source_title)

        return seed_outline_from_pages(page_count, source_title)

    def _extract_bookmarks(self, reader: PdfReader) -> list[tuple[int, str, int]]:
        outline = getattr(reader, "outline", None) or getattr(reader, "outlines", None)
        if not outline:
            return []
        out: list[tuple[int, str, int]] = []

        def walk(items: list[Any], depth: int):
            for it in items:
                if isinstance(it, list):
                    walk(it, depth + 1)
                    continue
                try:
                    title = str(getattr(it, "title", "")).strip()
                    page = int(reader.get_destination_page_number(it)) + 1
                    if title and page >= 1:
                        out.append((depth, title, page))
                except Exception:
                    continue

        if isinstance(outline, list):
            walk(outline, 1)
        return out

    def _infer_headings(self, reader: PdfReader) -> list[tuple[int, str, int]]:
        candidates: list[tuple[int, str, int, float]] = []
        chapter_re = re.compile(r"^(chapter|part|section)\b", re.IGNORECASE)

        for idx, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue
            lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
            for ln in lines[:25]:
                if len(ln) < 4 or len(ln) > 90:
                    continue
                words = ln.split()
                if len(words) > 14:
                    continue
                score = 0.0
                if chapter_re.match(ln):
                    score += 4.0
                if ln == ln.upper() and any(c.isalpha() for c in ln):
                    score += 2.0
                title_ratio = sum(1 for w in words if w[:1].isupper()) / max(1, len(words))
                score += title_ratio
                if ln.endswith(":") or ln.endswith("."):
                    score -= 1.0
                if score >= 2.6:
                    depth = 1 if chapter_re.match(ln) else 2
                    candidates.append((depth, ln, idx, score))
                    break

        # dedupe nearby pages, keep stronger signal
        picked: list[tuple[int, str, int]] = []
        last_page = -99
        for depth, title, page, _score in sorted(candidates, key=lambda x: (x[2], -x[3])):
            if page - last_page < 2:
                continue
            picked.append((depth, title, page))
            last_page = page
        return picked

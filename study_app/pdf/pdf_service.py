from __future__ import annotations

from typing import Any

from pypdf import PdfReader

from study_app.services.outline_service import entries_from_bookmarks, seed_outline_single_section


class PdfService:
    def inspect(self, path: str) -> tuple[int, int]:
        from pathlib import Path

        p = Path(path)
        reader = PdfReader(path)
        return p.stat().st_size, len(reader.pages)

    def generate_outline_entries(self, path: str, source_title: str, page_count: int) -> list[dict]:
        reader = PdfReader(path)
        bookmarks = self._extract_bookmarks_from_outline(reader)
        if bookmarks:
            return entries_from_bookmarks(bookmarks, page_count, source_title)
        # Required fallback: if no outline/bookmarks, use one full-document section.
        return seed_outline_single_section(page_count, source_title)

    def _extract_bookmarks_from_outline(self, reader: PdfReader) -> list[tuple[int, str, int]]:
        """Return (depth, title, page_1_based) tuples from reader.outline recursively."""
        outline = getattr(reader, "outline", None)
        if not outline:
            return []

        out: list[tuple[int, str, int]] = []

        def walk(nodes: list[Any], depth: int) -> None:
            for node in nodes:
                if isinstance(node, list):
                    walk(node, depth + 1)
                    continue
                try:
                    title = str(getattr(node, "title", "")).strip()
                    if not title:
                        continue
                    # pypdf returns 0-based page index for destinations.
                    page = int(reader.get_destination_page_number(node)) + 1
                    if page >= 1:
                        out.append((depth, title, page))
                except Exception:
                    continue

        if isinstance(outline, list):
            walk(outline, 1)
        return out

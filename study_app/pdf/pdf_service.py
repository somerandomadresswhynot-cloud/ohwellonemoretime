from __future__ import annotations

import re
from typing import Any

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - environment fallback
    PdfReader = None

from study_app.services.outline_service import entries_from_bookmarks, seed_outline_single_section


class PdfService:
    def __init__(self) -> None:
        self._text_layer_probe_cache: dict[str, dict] = {}

    def inspect(self, path: str) -> tuple[int, int]:
        from pathlib import Path

        p = Path(path)
        if PdfReader is None:
            raise RuntimeError("pypdf is required for PDF inspection but is not installed")
        reader = PdfReader(path)
        return p.stat().st_size, len(reader.pages)

    def generate_outline_entries(self, path: str, source_title: str, page_count: int) -> list[dict]:
        if PdfReader is None:
            raise RuntimeError("pypdf is required for outline extraction but is not installed")
        reader = PdfReader(path)
        bookmarks = self._extract_bookmarks_from_outline(reader)
        if bookmarks:
            return entries_from_bookmarks(bookmarks, page_count, source_title)
        # Required fallback: if no outline/bookmarks, use one full-document section.
        return seed_outline_single_section(page_count, source_title)

    def probe_text_layer(self, path: str, pages_to_sample: int = 6) -> dict:
        if path in self._text_layer_probe_cache:
            return dict(self._text_layer_probe_cache[path])
        result = {"has_text_layer": False, "sampled_pages": 0, "text_pages": 0, "error": ""}
        if PdfReader is None:
            result["error"] = "pypdf not installed"
            self._text_layer_probe_cache[path] = dict(result)
            return dict(result)
        try:
            reader = PdfReader(path)
            total = len(reader.pages)
            if total <= 0:
                self._text_layer_probe_cache[path] = result
                return dict(result)
            probes = min(max(1, int(pages_to_sample)), total)
            # sample front, middle, and back by stride
            stride = max(1, total // probes)
            sampled_idx = sorted({min(total - 1, i * stride) for i in range(probes)})
            text_pages = 0
            for idx in sampled_idx:
                txt = (reader.pages[idx].extract_text() or "").strip()
                if txt:
                    text_pages += 1
            result = {
                "has_text_layer": text_pages > 0,
                "sampled_pages": len(sampled_idx),
                "text_pages": text_pages,
                "error": "",
            }
        except Exception as exc:
            result["error"] = str(exc)
        self._text_layer_probe_cache[path] = dict(result)
        return dict(result)

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

    def extract_page_text(self, path: str, page_number: int) -> str:
        return self.extract_page_range_text(path, page_number, page_number)

    def extract_page_range_text(self, path: str, start_page: int, end_page: int) -> str:
        if PdfReader is None:
            raise RuntimeError("pypdf is required for text extraction but is not installed")
        reader = PdfReader(path)
        total_pages = len(reader.pages)
        if total_pages <= 0:
            return ""
        start = max(1, min(total_pages, int(start_page)))
        end = max(start, min(total_pages, int(end_page)))
        chunks: list[str] = []
        for idx in range(start - 1, end):
            raw = reader.pages[idx].extract_text() or ""
            clean = self._normalize_extracted_text(raw)
            if clean:
                chunks.append(clean)
        return "\n\n".join(chunks).strip()

    def _normalize_extracted_text(self, text: str) -> str:
        if not text:
            return ""
        out = text.replace("\r\n", "\n").replace("\r", "\n")
        out = out.replace("\u00ad\n", "").replace("\u00ad", "")
        out = re.sub(r"(?<=\w)-\n(?=\w)", "", out)
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        lines = out.split("\n")
        merged: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                merged.append("")
                continue
            if not merged or not merged[-1]:
                merged.append(stripped)
                continue
            prev = merged[-1]
            should_join = (
                prev and not prev.endswith((".", "!", "?", ":", ";"))
                and stripped and stripped[0].islower()
            )
            merged[-1] = f"{prev} {stripped}" if should_join else f"{prev}\n{stripped}"
        normalized = "\n".join(merged)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()

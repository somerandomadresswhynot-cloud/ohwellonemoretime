from __future__ import annotations

from typing import Any

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - environment fallback
    PdfReader = None

from study_app.services.outline_service import entries_from_bookmarks, seed_outline_single_section


class PdfService:
    def __init__(self) -> None:
        self._text_layer_probe_cache: dict[str, dict] = {}
        self._text_anchor_rect_cache: dict[tuple[str, int, str, str, str], list[dict]] = {}

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

    def resolve_text_anchor_rects(
        self,
        path: str,
        page: int,
        text_exact: str,
        text_prefix: str = "",
        text_suffix: str = "",
    ) -> list[dict]:
        key = (path, int(page), str(text_exact or ""), str(text_prefix or ""), str(text_suffix or ""))
        if key in self._text_anchor_rect_cache:
            return list(self._text_anchor_rect_cache[key])
        if PdfReader is None:
            return []
        exact = " ".join((text_exact or "").split()).strip()
        if not exact:
            return []
        try:
            reader = PdfReader(path)
            page_idx = max(0, int(page) - 1)
            if page_idx >= len(reader.pages):
                return []
            p = reader.pages[page_idx]
            media = p.mediabox
            page_w = max(1.0, float(media.width))
            page_h = max(1.0, float(media.height))
            chunks: list[dict] = []

            def _visitor(txt, _cm, tm, _font_dict, font_size):
                if not txt:
                    return
                s = str(txt)
                if not s.strip():
                    return
                x = float(tm[4]) if tm and len(tm) > 5 else 0.0
                y = float(tm[5]) if tm and len(tm) > 5 else 0.0
                fs = max(6.0, float(font_size or 10.0))
                chunks.append({"text": s, "x": x, "y": y, "fs": fs})

            p.extract_text(visitor_text=_visitor)
            if not chunks:
                return []
            joined = "".join(c["text"] for c in chunks)
            # Normalize whitespace to improve anchor matching against PDF extraction variance.
            norm_chars: list[str] = []
            norm_to_raw: list[int] = []
            prev_space = False
            for idx, ch in enumerate(joined):
                if ch.isspace():
                    if prev_space:
                        continue
                    norm_chars.append(" ")
                    norm_to_raw.append(idx)
                    prev_space = True
                    continue
                prev_space = False
                norm_chars.append(ch)
                norm_to_raw.append(idx)
            while norm_chars and norm_chars[0] == " ":
                norm_chars.pop(0)
                norm_to_raw.pop(0)
            while norm_chars and norm_chars[-1] == " ":
                norm_chars.pop()
                norm_to_raw.pop()
            joined_norm = "".join(norm_chars)
            if not joined_norm or not norm_to_raw:
                return []
            search_start = 0
            matches: list[tuple[int, int]] = []
            while True:
                idx = joined_norm.find(exact, search_start)
                if idx < 0:
                    break
                matches.append((idx, idx + len(exact)))
                search_start = idx + 1
            if not matches:
                return []
            # Build character index map for chunk ranges.
            spans = []
            cursor = 0
            for c in chunks:
                s = c["text"]
                spans.append((cursor, cursor + len(s), c))
                cursor += len(s)
            prefix = " ".join((text_prefix or "").split()).strip()
            suffix = " ".join((text_suffix or "").split()).strip()

            def _score(match):
                a, b = match
                left = joined_norm[max(0, a - len(prefix) - 8):a] if prefix else ""
                right = joined_norm[b:b + len(suffix) + 8] if suffix else ""
                score = 0
                if prefix and prefix in left:
                    score += 1
                if suffix and suffix in right:
                    score += 1
                return score

            best = max(matches, key=_score)
            a, b = best
            raw_a = norm_to_raw[max(0, min(len(norm_to_raw) - 1, a))]
            raw_b = norm_to_raw[max(0, min(len(norm_to_raw) - 1, b - 1))] + 1
            rects: list[dict] = []
            for s0, s1, chunk in spans:
                if s1 <= raw_a or s0 >= raw_b:
                    continue
                overlap_start = max(s0, raw_a)
                overlap_end = min(s1, raw_b)
                overlap_text = chunk["text"][overlap_start - s0: overlap_end - s0]
                chars = max(1, len(overlap_text))
                fs = float(chunk["fs"])
                x = float(chunk["x"])
                y = float(chunk["y"])
                w = max(3.0, fs * 0.55 * chars)
                h = max(8.0, fs * 1.25)
                top = page_h - (y + (h * 0.9))
                rects.append({
                    "x": round(max(0.0, min(1.0, x / page_w)), 6),
                    "y": round(max(0.0, min(1.0, top / page_h)), 6),
                    "w": round(max(0.0, min(1.0, w / page_w)), 6),
                    "h": round(max(0.0, min(1.0, h / page_h)), 6),
                })
            self._text_anchor_rect_cache[key] = list(rects)
            return rects
        except Exception:
            return []

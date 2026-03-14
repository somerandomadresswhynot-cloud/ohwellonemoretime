from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader


class PdfService:
    def inspect(self, path: str) -> tuple[int, int]:
        p = Path(path)
        reader = PdfReader(path)
        return p.stat().st_size, len(reader.pages)

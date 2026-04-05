from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

try:
    from pdfjs_viewer import PDFViewerWidget
except Exception:  # pragma: no cover - dependency may be unavailable in dev env
    PDFViewerWidget = None


class EmbeddedPdfViewer(QWidget):
    """Thin app-local wrapper over `pdfjs-viewer-pyside6`'s `PDFViewerWidget`."""

    def __init__(self) -> None:
        super().__init__()
        self.current_path = ""
        self._viewer = None
        self._last_page = 1
        self._last_zoom: int | str = "page-width"
        self._pending_page: int | None = None
        self._pending_page_attempts = 0
        self._selection_menu_handler: Callable | None = None
        self._active_highlights: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        if PDFViewerWidget is None:
            fallback = QLabel("PDF viewer unavailable: install `pdfjs-viewer-pyside6`.")
            fallback.setAlignment(Qt.AlignCenter)
            root.addWidget(fallback)
            self._fallback = fallback
            return

        self._viewer = PDFViewerWidget(preset="simple")
        try:
            self._viewer.page_changed.connect(self._on_page_changed)
        except Exception:
            pass
        self._viewer.setFocusPolicy(Qt.StrongFocus)
        self._viewer.setContextMenuPolicy(Qt.CustomContextMenu)
        self._viewer.customContextMenuRequested.connect(self._on_context_menu)

        root.addWidget(self._viewer, 1)

        controls = QHBoxLayout()
        self.zoom_out_btn = QPushButton("-")
        self.zoom_in_btn = QPushButton("+")
        self.zoom_pct = QSpinBox()
        self.zoom_pct.setRange(25, 400)
        self.zoom_pct.setValue(100)
        self.fit_width_btn = QPushButton("Fit Width")
        self.fit_page_btn = QPushButton("Fit Page")

        self.zoom_out_btn.clicked.connect(lambda: self.set_zoom(max(0.25, self.zoom_factor() - 0.1)))
        self.zoom_in_btn.clicked.connect(lambda: self.set_zoom(min(4.0, self.zoom_factor() + 0.1)))
        self.zoom_pct.valueChanged.connect(lambda v: self.set_zoom(v / 100.0))
        self.fit_width_btn.clicked.connect(self.set_fit_mode)
        self.fit_page_btn.clicked.connect(self.set_fit_page_mode)

        for w in [QLabel("Scale"), self.zoom_out_btn, self.zoom_pct, self.zoom_in_btn, self.fit_width_btn, self.fit_page_btn]:
            controls.addWidget(w)
        controls.addStretch()
        root.addLayout(controls)

    def _on_context_menu(self, _pos) -> None:
        if not self._selection_menu_handler:
            return
        self.get_selection_details(
            lambda details: self._selection_menu_handler(
                QCursor.pos(),
                details.get("selected_text", ""),
                int(details.get("page", self.view_state().get("page", 1))),
                details,
            )
        )

    def set_selection_menu_handler(self, handler) -> None:
        self._selection_menu_handler = handler

    def _on_page_changed(self, current: int, _total: int) -> None:
        self._last_page = max(1, int(current))

    def selected_text(self) -> str:
        try:
            return (QApplication.clipboard().text() or "").strip()
        except Exception:
            return ""

    def _browser_page(self):
        if not self._viewer:
            return None
        direct_page = getattr(self._viewer, "page", None)
        if callable(direct_page):
            try:
                page = direct_page()
                if page:
                    return page
            except Exception:
                pass
        for attr in ("webview", "view", "_webview", "_view", "browser"):
            candidate = getattr(self._viewer, attr, None)
            if not candidate:
                continue
            page_fn = getattr(candidate, "page", None)
            if callable(page_fn):
                try:
                    page = page_fn()
                    if page:
                        return page
                except Exception:
                    continue
        return None

    def _run_js(self, script: str, callback: Callable | None = None) -> bool:
        if not self._viewer:
            return False
        run_js = getattr(self._viewer, "runJavaScript", None)
        if callable(run_js):
            try:
                if callback:
                    run_js(script, callback)
                else:
                    run_js(script)
                return True
            except Exception:
                pass
        page = self._browser_page()
        if not page:
            return False
        run_js = getattr(page, "runJavaScript", None)
        if not callable(run_js):
            return False
        try:
            if callback:
                run_js(script, callback)
            else:
                run_js(script)
            return True
        except Exception:
            return False

    def _ensure_highlight_runtime(self) -> None:
        self._run_js(
            """
(() => {
  if (window.__studyHighlightRuntimeReady) return true;
  window.__studyHighlightRuntimeReady = true;
  window.__studyHighlightStore = [];
  const toHex = (v) => (typeof v === 'string' && v.trim()) ? v.trim() : '#fff59d';
  const ensureLayer = (pageDiv) => {
    let layer = pageDiv.querySelector('.study-highlight-layer');
    if (!layer) {
      layer = document.createElement('div');
      layer.className = 'study-highlight-layer';
      Object.assign(layer.style, {
        position: 'absolute', left: '0', top: '0', width: '100%', height: '100%',
        pointerEvents: 'none', zIndex: '15',
      });
      pageDiv.appendChild(layer);
    }
    return layer;
  };
  const render = () => {
    const app = window.PDFViewerApplication;
    const viewer = app && app.pdfViewer;
    if (!viewer) return;
    const pageViews = viewer._pages || [];
    for (const pv of pageViews) {
      if (!pv || !pv.div || !pv.viewport) continue;
      const pageDiv = pv.div;
      const layer = ensureLayer(pageDiv);
      layer.replaceChildren();
      const pageIndex = Number(pv.id || 1) - 1;
      const rows = window.__studyHighlightStore.filter((h) => Number(h.page_index) === pageIndex);
      for (const row of rows) {
        const opacity = Number(row.opacity ?? 0.35);
        const color = toHex(row.color_value || row.color || '#fff59d');
        for (const r of (row.rects || [])) {
          const x = Number(r.x), y = Number(r.y), w = Number(r.w), h = Number(r.h);
          if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) continue;
          const p1 = pv.viewport.convertToViewportPoint(x, y);
          const p2 = pv.viewport.convertToViewportPoint(x + w, y + h);
          const left = Math.min(p1[0], p2[0]);
          const top = Math.min(p1[1], p2[1]);
          const width = Math.abs(p2[0] - p1[0]);
          const height = Math.abs(p2[1] - p1[1]);
          if (width <= 0 || height <= 0) continue;
          const box = document.createElement('div');
          box.dataset.highlightId = String(row.id || '');
          Object.assign(box.style, {
            position: 'absolute', left: `${left}px`, top: `${top}px`,
            width: `${width}px`, height: `${height}px`,
            background: color, opacity: String(opacity),
            borderRadius: '2px', mixBlendMode: 'multiply', pointerEvents: 'none',
          });
          layer.appendChild(box);
        }
      }
    }
  };
  window.__studyRenderHighlights = render;
  const bus = window.PDFViewerApplication && window.PDFViewerApplication.eventBus;
  if (bus && !window.__studyHighlightEventsBound) {
    window.__studyHighlightEventsBound = true;
    ['pagerendered','textlayerrendered','scalechanging','updateviewarea'].forEach((evt) => {
      try { bus.on(evt, () => window.requestAnimationFrame(render)); } catch (_e) {}
    });
  }
  window.requestAnimationFrame(render);
  return true;
})();
            """
        )

    def get_selection_details(self, callback: Callable[[dict], None]) -> None:
        def _default() -> None:
            callback(
                {
                    "selected_text": self.selected_text(),
                    "page": int(self.view_state().get("page", 1)),
                    "rects_by_page": [],
                    "page_index": max(0, int(self.view_state().get("page", 1)) - 1),
                }
            )

        self._ensure_highlight_runtime()
        ok = self._run_js(
            """
(() => {
  const out = { selected_text: '', page: 1, page_index: 0, rects_by_page: [] };
  const sel = window.getSelection && window.getSelection();
  if (!sel || !sel.rangeCount) return out;
  const text = (sel.toString() || '').replace(/\\s+/g, ' ').trim();
  if (!text) return out;
  out.selected_text = text;
  const app = window.PDFViewerApplication;
  const viewer = app && app.pdfViewer;
  if (!viewer) return out;
  const byPage = new Map();
  const range = sel.getRangeAt(0);
  for (const rawRect of Array.from(range.getClientRects())) {
    if (!rawRect || rawRect.width <= 0 || rawRect.height <= 0) continue;
    const cx = rawRect.left + (rawRect.width / 2);
    const cy = rawRect.top + (rawRect.height / 2);
    const el = document.elementFromPoint(cx, cy);
    const pageDiv = el && el.closest && el.closest('.page');
    if (!pageDiv) continue;
    const pageNum = Number(pageDiv.dataset.pageNumber || pageDiv.getAttribute('data-page-number') || 1);
    const pageView = viewer.getPageView(pageNum - 1);
    if (!pageView || !pageView.viewport) continue;
    const pageRect = pageDiv.getBoundingClientRect();
    const left = rawRect.left - pageRect.left;
    const top = rawRect.top - pageRect.top;
    const right = rawRect.right - pageRect.left;
    const bottom = rawRect.bottom - pageRect.top;
    const a = pageView.viewport.convertToPdfPoint(left, top);
    const b = pageView.viewport.convertToPdfPoint(right, bottom);
    const x = Math.min(a[0], b[0]);
    const y = Math.min(a[1], b[1]);
    const w = Math.abs(b[0] - a[0]);
    const h = Math.abs(b[1] - a[1]);
    if (!(w > 0 && h > 0)) continue;
    const row = byPage.get(pageNum) || { page: pageNum, page_index: pageNum - 1, rects: [] };
    row.rects.push({ x, y, w, h });
    byPage.set(pageNum, row);
  }
  out.rects_by_page = Array.from(byPage.values()).sort((a,b) => a.page - b.page);
  if (out.rects_by_page.length > 0) {
    out.page = Number(out.rects_by_page[0].page);
    out.page_index = Number(out.rects_by_page[0].page_index);
  } else {
    out.page = Number((viewer.currentPageNumber || 1));
    out.page_index = Math.max(0, out.page - 1);
  }
  return out;
})();
            """,
            callback=lambda result: callback(result or {}),
        )
        if not ok:
            _default()

    def set_text_highlights(self, highlights: list[dict]) -> None:
        self._active_highlights = list(highlights or [])
        self._ensure_highlight_runtime()
        payload = json.dumps(self._active_highlights, separators=(",", ":"))
        self._run_js(
            f"""
(() => {{
  window.__studyHighlightStore = {payload};
  if (typeof window.__studyRenderHighlights === 'function') {{
    window.requestAnimationFrame(window.__studyRenderHighlights);
  }}
  return true;
}})();
            """
        )

    def prime_path(self, path: str) -> None:
        # Intentionally no-op for PDF.js viewer backend.
        _ = path

    def zoom_factor(self) -> float:
        try:
            return float(self.zoom_pct.value()) / 100.0
        except Exception:
            return 1.0

    def _reload(self, *, page: int | None = None, zoom: int | str | None = None) -> None:
        if not self._viewer or not self.current_path:
            return
        load_page = page if page is not None else self._last_page
        load_zoom = zoom if zoom is not None else self._last_zoom
        self._viewer.load_pdf(self.current_path, page=max(1, int(load_page)), zoom=load_zoom)

    def set_multi_page_mode(self) -> None:
        # PDF.js viewer handles continuous scrolling natively.
        return

    def set_single_page_mode(self) -> None:
        # No explicit single-page API needed for current integration.
        return

    def load_if_needed(self, path: str) -> bool:
        if not self._viewer:
            return False
        if not path or not Path(path).exists():
            return False
        if path == self.current_path:
            return False
        self.current_path = path
        self._last_page = 1
        self._last_zoom = "page-width"
        self._pending_page = None
        self._pending_page_attempts = 0
        self._viewer.load_pdf(path, page=1, zoom=self._last_zoom)
        QTimer.singleShot(120, lambda: self.set_text_highlights(self._active_highlights))
        return True

    def set_fit_mode(self) -> None:
        self._last_zoom = "page-width"
        self._reload(zoom=self._last_zoom)

    def set_fit_page_mode(self) -> None:
        self._last_zoom = "page-fit"
        self._reload(zoom=self._last_zoom)

    def set_page(self, page: int, location=None) -> None:
        _ = location
        self._last_page = max(1, int(page))
        if self._viewer:
            self._pending_page = self._last_page
            self._pending_page_attempts = 0
            self._viewer.goto_page(self._last_page)
            # Some backends ignore immediate goto while a fresh PDF load is settling.
            # Retry for a short window until the target page is confirmed.
            QTimer.singleShot(0, self._apply_pending_page)
            QTimer.singleShot(90, self._apply_pending_page)

    def _apply_pending_page(self) -> None:
        if not self._viewer or self._pending_page is None:
            return
        page = max(1, int(self._pending_page))
        self._viewer.goto_page(page)
        current_page = None
        try:
            current_page = int(self._viewer.get_current_page())
        except Exception:
            current_page = None
        if current_page == page:
            self._pending_page = None
            self._pending_page_attempts = 0
            return
        self._pending_page_attempts += 1
        if self._pending_page_attempts >= 12:
            self._pending_page = None
            self._pending_page_attempts = 0
            return
        QTimer.singleShot(90, self._apply_pending_page)

    def set_zoom(self, factor: float) -> None:
        zoom_pct = max(25, min(400, int(round(float(factor) * 100))))
        if isinstance(self._last_zoom, int) and int(self._last_zoom) == zoom_pct:
            self.zoom_pct.blockSignals(True)
            self.zoom_pct.setValue(zoom_pct)
            self.zoom_pct.blockSignals(False)
            return
        self.zoom_pct.blockSignals(True)
        self.zoom_pct.setValue(zoom_pct)
        self.zoom_pct.blockSignals(False)
        self._last_zoom = zoom_pct
        self._reload(zoom=zoom_pct)

    def view_state(self) -> dict:
        page = self._last_page
        if self._viewer:
            try:
                page = max(1, int(self._viewer.get_current_page()))
            except Exception:
                pass
        return {"page": page, "location": (0.0, 0.0)}

    # Legacy methods kept as no-ops so higher-level screens can ignore old overlay paths.
    def set_annotation_tool(self, tool: str) -> None:
        _ = tool

    def set_area_created_handler(self, handler) -> None:
        _ = handler

    def set_highlight_hit_handler(self, handler) -> None:
        _ = handler

    def set_overlay_highlights(self, highlights: list[dict]) -> None:
        # Legacy API now maps to page-space text highlights for compatibility.
        self.set_text_highlights(highlights)

    def toggle_fullscreen(self) -> None:
        if not self._viewer:
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()


# Backward-compatible name for callsites still importing PersistentPdfViewer.
PersistentPdfViewer = EmbeddedPdfViewer

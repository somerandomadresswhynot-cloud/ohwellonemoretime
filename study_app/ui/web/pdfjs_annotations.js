(function () {
  function toRectFromPdf(rect, viewport) {
    const pts = viewport.convertToViewportRectangle([rect.x, rect.y, rect.x + rect.width, rect.y + rect.height]);
    const left = Math.min(pts[0], pts[2]);
    const top = Math.min(pts[1], pts[3]);
    const width = Math.abs(pts[2] - pts[0]);
    const height = Math.abs(pts[3] - pts[1]);
    return { left, top, width, height };
  }

  class OHWPdfAnnotations {
    constructor(pdfApp, hooks) {
      this.pdfApp = pdfApp;
      this.hooks = hooks;
      this.tool = 'select_text';
      this.annotationsByPage = new Map();
      this.layersByPage = new Map();
      this.drag = null;
      this._bindEvents();
    }

    setTool(tool) {
      this.tool = tool || 'select_text';
      this._syncPointerMode();
    }

    loadAnnotations(payload) {
      this.annotationsByPage = new Map();
      for (const ann of (payload || [])) {
        const pageIndex = Number(ann.page_index || 0);
        const bucket = this.annotationsByPage.get(pageIndex) || [];
        bucket.push(ann);
        this.annotationsByPage.set(pageIndex, bucket);
      }
      this.renderAll();
    }

    renderAll() {
      const viewer = this.pdfApp.pdfViewer;
      if (!viewer) return;
      for (let i = 0; i < viewer.pagesCount; i++) {
        this._ensureLayer(i);
        this._renderPage(i);
      }
      this._syncPointerMode();
    }

    _ensureLayer(pageIndex) {
      if (this.layersByPage.has(pageIndex)) return this.layersByPage.get(pageIndex);
      const pageView = this.pdfApp.pdfViewer.getPageView(pageIndex);
      if (!pageView || !pageView.div) return null;
      const layer = document.createElement('div');
      layer.className = 'ohw-annotation-layer';
      pageView.div.appendChild(layer);
      this.layersByPage.set(pageIndex, layer);
      return layer;
    }

    _renderPage(pageIndex) {
      const layer = this._ensureLayer(pageIndex);
      const pageView = this.pdfApp.pdfViewer.getPageView(pageIndex);
      if (!layer || !pageView) return;
      layer.innerHTML = '';
      const anns = this.annotationsByPage.get(pageIndex) || [];
      for (const ann of anns) {
        for (const rect of (ann.rects || [])) {
          const px = toRectFromPdf(rect, pageView.viewport);
          const el = document.createElement('div');
          el.className = 'ohw-annotation-rect';
          el.dataset.annotationId = String(ann.annotation_id || '');
          el.style.left = `${px.left}px`;
          el.style.top = `${px.top}px`;
          el.style.width = `${px.width}px`;
          el.style.height = `${px.height}px`;
          const color = ann.color || '#2d9cdb';
          const opacity = Math.max(0, Math.min(1, Number(ann.opacity ?? 0.35)));
          el.style.borderColor = color;
          el.style.background = `${color}${Math.round(opacity * 255).toString(16).padStart(2, '0')}`;
          layer.appendChild(el);
        }
      }
    }

    _bindEvents() {
      const viewerEl = this.pdfApp.pdfViewer?.viewer;
      if (!viewerEl) return;
      viewerEl.addEventListener('mousedown', (ev) => this._onMouseDown(ev), true);
      viewerEl.addEventListener('mousemove', (ev) => this._onMouseMove(ev), true);
      window.addEventListener('mouseup', (ev) => this._onMouseUp(ev), true);
      viewerEl.addEventListener('click', (ev) => this._onClick(ev), true);
      this.pdfApp.eventBus.on('pagerendered', (evt) => {
        const pageIndex = Math.max(0, Number(evt.pageNumber || 1) - 1);
        this._ensureLayer(pageIndex);
        this._renderPage(pageIndex);
        this._syncPointerMode();
      });
      this.pdfApp.eventBus.on('scalechanging', () => this.renderAll());
    }

    _syncPointerMode() {
      for (const layer of this.layersByPage.values()) {
        layer.style.pointerEvents = (this.tool === 'area' || this.tool === 'erase') ? 'auto' : 'none';
      }
    }

    _onMouseDown(ev) {
      if (this.tool !== 'area') return;
      const pageEl = ev.target.closest('.page');
      if (!pageEl) return;
      const pageNumber = Number(pageEl.dataset.pageNumber || 1);
      const pageIndex = pageNumber - 1;
      const layer = this._ensureLayer(pageIndex);
      if (!layer) return;
      const r = pageEl.getBoundingClientRect();
      this.drag = {
        pageIndex,
        pageNumber,
        pageEl,
        startX: ev.clientX - r.left,
        startY: ev.clientY - r.top,
        draft: document.createElement('div'),
      };
      this.drag.draft.className = 'ohw-area-draft';
      layer.appendChild(this.drag.draft);
      ev.preventDefault();
      ev.stopPropagation();
    }

    _onMouseMove(ev) {
      if (!this.drag) return;
      const r = this.drag.pageEl.getBoundingClientRect();
      const x = Math.max(0, Math.min(r.width, ev.clientX - r.left));
      const y = Math.max(0, Math.min(r.height, ev.clientY - r.top));
      const left = Math.min(this.drag.startX, x);
      const top = Math.min(this.drag.startY, y);
      const width = Math.abs(x - this.drag.startX);
      const height = Math.abs(y - this.drag.startY);
      Object.assign(this.drag.draft.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
      ev.preventDefault();
      ev.stopPropagation();
    }

    _onMouseUp(ev) {
      if (!this.drag) return;
      const pageView = this.pdfApp.pdfViewer.getPageView(this.drag.pageIndex);
      const r = this.drag.pageEl.getBoundingClientRect();
      const x = Math.max(0, Math.min(r.width, ev.clientX - r.left));
      const y = Math.max(0, Math.min(r.height, ev.clientY - r.top));
      const left = Math.min(this.drag.startX, x);
      const top = Math.min(this.drag.startY, y);
      const width = Math.abs(x - this.drag.startX);
      const height = Math.abs(y - this.drag.startY);
      this.drag.draft.remove();
      if (width >= 4 && height >= 4 && pageView) {
        const a = pageView.viewport.convertToPdfPoint(left, top + height);
        const b = pageView.viewport.convertToPdfPoint(left + width, top);
        const rect = {
          x: Math.min(a[0], b[0]),
          y: Math.min(a[1], b[1]),
          width: Math.abs(b[0] - a[0]),
          height: Math.abs(b[1] - a[1]),
          coord_space: 'page',
        };
        this.hooks.onAreaCreated?.({ page_number: this.drag.pageNumber, page_index: this.drag.pageIndex, rect });
      }
      this.drag = null;
      ev.preventDefault();
      ev.stopPropagation();
    }

    _onClick(ev) {
      if (this.tool !== 'erase') return;
      const ann = ev.target.closest('.ohw-annotation-rect');
      if (!ann) return;
      this.hooks.onAnnotationDeleted?.(ann.dataset.annotationId || '');
      ev.preventDefault();
      ev.stopPropagation();
    }
  }

  window.OHWPdfAnnotations = OHWPdfAnnotations;
})();

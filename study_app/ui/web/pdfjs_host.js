const statusEl = document.getElementById('toolbar-status');
const root = document.getElementById('viewer-root');
const state = {
  pdfjsLib: null,
  TextLayer: null,
  pdfDoc: null,
  pageMode: 'vertical',
  scale: 'page-width',
  tool: 'select_text',
  pyBridge: null,
  annotationsByPage: new Map(),
  pages: new Map(),
  selectedText: '',
  currentPage: 1,
};

function emit(name, ...args) {
  const b = state.pyBridge;
  if (!b || typeof b[name] !== 'function') return;
  b[name](...args);
}

function setStatus(msg) { statusEl.textContent = msg; }
function safeJson(v) { return JSON.stringify(v || {}); }

function pageFromNode(node) {
  let cur = node;
  while (cur && cur !== document.body) {
    if (cur.classList && cur.classList.contains('pdf-page-shell')) return Number(cur.dataset.pageNumber || 1);
    cur = cur.parentNode;
  }
  return state.currentPage;
}

function projectRect(rect, viewport) {
  if (rect.coord_space === 'normalized') {
    return {
      left: rect.x * viewport.width,
      top: rect.y * viewport.height,
      width: rect.width * viewport.width,
      height: rect.height * viewport.height,
    };
  }
  const pts = viewport.convertToViewportRectangle([rect.x, rect.y, rect.x + rect.width, rect.y + rect.height]);
  const left = Math.min(pts[0], pts[2]);
  const top = Math.min(pts[1], pts[3]);
  const width = Math.abs(pts[2] - pts[0]);
  const height = Math.abs(pts[3] - pts[1]);
  return { left, top, width, height };
}

function renderAnnotations() {
  for (const [pageNo, refs] of state.pages.entries()) {
    refs.annLayer.innerHTML = '';
    const anns = state.annotationsByPage.get(pageNo - 1) || [];
    for (const ann of anns) {
      for (const rect of (ann.rects || [])) {
        const px = projectRect(rect, refs.viewport);
        const el = document.createElement('div');
        el.className = 'annotation-rect';
        el.dataset.annotationId = String(ann.annotation_id || '');
        el.style.left = `${px.left}px`;
        el.style.top = `${px.top}px`;
        el.style.width = `${px.width}px`;
        el.style.height = `${px.height}px`;
        const color = ann.color || '#2d9cdb';
        const opacity = Math.max(0, Math.min(1, Number(ann.opacity ?? 0.35)));
        el.style.borderColor = color;
        el.style.background = `${color}${Math.round(opacity * 255).toString(16).padStart(2, '0')}`;
        refs.annLayer.appendChild(el);
      }
    }
  }
}

async function renderPdf() {
  if (!state.pdfDoc) return;
  root.innerHTML = '';
  state.pages.clear();
  const count = state.pdfDoc.numPages;
  for (let i = 1; i <= count; i++) {
    const page = await state.pdfDoc.getPage(i);
    const shell = document.createElement('div');
    shell.className = 'pdf-page-shell';
    shell.dataset.pageNumber = String(i);

    const vp = page.getViewport({ scale: 1 });
    const targetWidth = Math.max(400, root.clientWidth - 40);
    const scale = (state.scale === 'page-width') ? (targetWidth / vp.width) : (typeof state.scale === 'number' ? state.scale : 1);
    const viewport = page.getViewport({ scale });

    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width);
    canvas.height = Math.floor(viewport.height);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    shell.style.width = `${viewport.width}px`;
    shell.style.height = `${viewport.height}px`;

    const textLayerDiv = document.createElement('div');
    textLayerDiv.className = 'textLayer';
    textLayerDiv.style.width = `${viewport.width}px`;
    textLayerDiv.style.height = `${viewport.height}px`;

    const annLayer = document.createElement('div');
    annLayer.className = 'annotation-layer';
    annLayer.dataset.pageNumber = String(i);

    shell.append(canvas, textLayerDiv, annLayer);
    root.append(shell);

    await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
    const textContent = await page.getTextContent();
    const textLayer = new state.TextLayer({ textContentSource: textContent, container: textLayerDiv, viewport });
    await textLayer.render();

    state.pages.set(i, { page, viewport, shell, canvas, textLayerDiv, annLayer });
  }
  renderAnnotations();
}

function applyTool(tool) {
  state.tool = tool || 'select_text';
  document.body.classList.remove('tool-select_text', 'tool-pan', 'tool-area', 'tool-erase');
  document.body.classList.add(`tool-${state.tool}`);
  for (const refs of state.pages.values()) {
    refs.annLayer.style.pointerEvents = (state.tool === 'area' || state.tool === 'erase') ? 'auto' : 'none';
  }
}

function installInteractions() {
  let drag = null;
  root.addEventListener('mousedown', (ev) => {
    if (state.tool !== 'area') return;
    const shell = ev.target.closest('.pdf-page-shell');
    if (!shell) return;
    const pageNo = Number(shell.dataset.pageNumber || 1);
    const r = shell.getBoundingClientRect();
    drag = { pageNo, startX: ev.clientX - r.left, startY: ev.clientY - r.top, shell, draft: document.createElement('div') };
    drag.draft.className = 'area-draft';
    shell.appendChild(drag.draft);
    ev.preventDefault();
  });

  root.addEventListener('mousemove', (ev) => {
    if (!drag) return;
    const r = drag.shell.getBoundingClientRect();
    const x = Math.max(0, Math.min(r.width, ev.clientX - r.left));
    const y = Math.max(0, Math.min(r.height, ev.clientY - r.top));
    const left = Math.min(drag.startX, x);
    const top = Math.min(drag.startY, y);
    const width = Math.abs(x - drag.startX);
    const height = Math.abs(y - drag.startY);
    Object.assign(drag.draft.style, { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` });
  });

  window.addEventListener('mouseup', (ev) => {
    if (!drag) return;
    const refs = state.pages.get(drag.pageNo);
    const r = drag.shell.getBoundingClientRect();
    const x = Math.max(0, Math.min(r.width, ev.clientX - r.left));
    const y = Math.max(0, Math.min(r.height, ev.clientY - r.top));
    const left = Math.min(drag.startX, x);
    const top = Math.min(drag.startY, y);
    const width = Math.abs(x - drag.startX);
    const height = Math.abs(y - drag.startY);
    drag.draft.remove();
    if (width >= 4 && height >= 4 && refs) {
      const a = refs.viewport.convertToPdfPoint(left, top + height);
      const b = refs.viewport.convertToPdfPoint(left + width, top);
      const rect = { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), width: Math.abs(b[0] - a[0]), height: Math.abs(b[1] - a[1]), coord_space: 'page' };
      emit('emit_annotation_created', safeJson({ page_number: drag.pageNo, page_index: drag.pageNo - 1, rect }));
    }
    drag = null;
  });

  root.addEventListener('click', (ev) => {
    if (state.tool !== 'erase') return;
    const ann = ev.target.closest('.annotation-rect');
    if (!ann) return;
    emit('emit_annotation_deleted', ann.dataset.annotationId || '');
    ev.preventDefault();
  });

  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    const text = sel ? String(sel.toString() || '') : '';
    state.selectedText = text;
    const page_number = sel && sel.anchorNode ? pageFromNode(sel.anchorNode) : state.currentPage;
    emit('emit_selection_changed', text, safeJson({ page_number, page_index: page_number - 1 }));
  });

  root.addEventListener('scroll', () => {
    const midpoint = root.scrollTop + (root.clientHeight / 2);
    let best = 1;
    let bestDist = Infinity;
    for (const [pageNo, refs] of state.pages.entries()) {
      const y = refs.shell.offsetTop + refs.shell.clientHeight / 2;
      const d = Math.abs(y - midpoint);
      if (d < bestDist) { bestDist = d; best = pageNo; }
    }
    if (best !== state.currentPage) {
      state.currentPage = best;
      emit('emit_page_changed', best);
    }
  });
}

function installHostApi() {
  window.pdfHost = {
    async openPdf(filePathOrUrl, initialPage = null) {
      try {
        let url = String(filePathOrUrl || '');
        if (!url.startsWith('file://')) {
          const normalized = url.replaceAll('\\', '/');
          url = `file:///${normalized.replace(/^\/+/, '')}`;
        }
        state.pdfDoc = await state.pdfjsLib.getDocument({ url }).promise;
        await renderPdf();
        setStatus(`Loaded ${state.pdfDoc.numPages} pages`);
        if (initialPage) this.goToPage(initialPage);
      } catch (err) {
        setStatus(`Failed to open PDF: ${err && err.message ? err.message : err}`);
        throw err;
      }
    },
    goToPage(pageNumber) {
      const refs = state.pages.get(Number(pageNumber));
      if (!refs) return;
      root.scrollTop = refs.shell.offsetTop;
      state.currentPage = Number(pageNumber);
      emit('emit_page_changed', state.currentPage);
    },
    setZoom(modeOrValue) {
      if (typeof modeOrValue === 'number') state.scale = modeOrValue;
      else state.scale = (modeOrValue === 'page-fit') ? 1 : 'page-width';
      renderPdf();
    },
    setTool(tool) { applyTool(tool); },
    loadAnnotations(payload) {
      state.annotationsByPage = new Map();
      for (const ann of (payload || [])) {
        const pageIndex = Number(ann.page_index || 0);
        const arr = state.annotationsByPage.get(pageIndex) || [];
        arr.push(ann);
        state.annotationsByPage.set(pageIndex, arr);
      }
      renderAnnotations();
    },
    requestSelectedText() {
      emit('emit_selection_changed', state.selectedText, safeJson({ page_number: state.currentPage, page_index: state.currentPage - 1 }));
    },
    copySelectedText() { document.execCommand('copy'); },
    clearSelection() {
      const sel = window.getSelection();
      if (sel) sel.removeAllRanges();
      state.selectedText = '';
    },
    setPageMode(mode) { state.pageMode = mode || 'vertical'; },
  };
}

async function loadPdfJsModule() {
  const candidates = [
    './vendor/pdfjs/build/pdf.mjs',
    '../../../third_party/pdfjs/build/pdf.mjs',
  ];
  let lastErr = null;
  for (const candidate of candidates) {
    try {
      const mod = await import(candidate);
      const workerSrc = new URL(candidate.replace('pdf.mjs', 'pdf.worker.mjs'), import.meta.url).toString();
      mod.GlobalWorkerOptions.workerSrc = workerSrc;
      return { pdfjsLib: mod, TextLayer: mod.TextLayer };
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error(`Unable to load PDF.js assets from expected locations. Last error: ${lastErr}`);
}

export async function bootPdfHost() {
  setStatus('Loading PDF.js host…');
  installInteractions();
  installHostApi();

  try {
    const loaded = await loadPdfJsModule();
    state.pdfjsLib = loaded.pdfjsLib;
    state.TextLayer = loaded.TextLayer;
  } catch (err) {
    setStatus(`Failed to load PDF.js assets: ${err && err.message ? err.message : err}`);
    return;
  }

  if (typeof QWebChannel !== 'function' || !window.qt || !qt.webChannelTransport) {
    setStatus('QWebChannel unavailable; cannot connect bridge');
    return;
  }

  new QWebChannel(qt.webChannelTransport, (channel) => {
    state.pyBridge = channel.objects.pyBridge;
    applyTool('select_text');
    emit('emit_viewer_ready', safeJson({
      nativeTextSelection: true,
      nativeCopy: true,
      pageCoordinateAnnotations: true,
      tools: ['select_text', 'pan', 'area', 'erase'],
    }));
    setStatus('PDF.js host ready');
  });
}

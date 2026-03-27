(function () {
  function waitForPdfViewerApp(timeoutMs) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeoutMs;
      const tick = () => {
        const app = window.PDFViewerApplication;
        const ready = app && app.initialized && app.eventBus && app.pdfViewer;
        if (ready) return resolve(app);
        if (Date.now() > deadline) return reject(new Error('PDFViewerApplication not ready'));
        setTimeout(tick, 50);
      };
      tick();
    });
  }

  function detectSelectionPageNumber(selection, fallbackPage) {
    let node = selection && selection.anchorNode;
    while (node && node !== document.body) {
      if (node.classList && node.classList.contains('page')) {
        return Number(node.dataset.pageNumber || fallbackPage || 1);
      }
      node = node.parentNode;
    }
    return fallbackPage || 1;
  }

  window.__ohwInstallBridge = async function __ohwInstallBridge(config) {
    const app = await waitForPdfViewerApp(config.timeoutMs || 15000);
    if (typeof QWebChannel !== 'function' || !window.qt || !qt.webChannelTransport) {
      throw new Error('QWebChannel unavailable');
    }

    new QWebChannel(qt.webChannelTransport, (channel) => {
      const pyBridge = channel.objects[config.pyBridgeObjectName || 'pyBridge'];
      const annotations = new window.OHWPdfAnnotations(app, {
        onAreaCreated: (payload) => pyBridge.emit_annotation_created(JSON.stringify(payload || {})),
        onAnnotationDeleted: (annotationId) => pyBridge.emit_annotation_deleted(String(annotationId || '')),
      });

      function emitSelection() {
        const sel = window.getSelection();
        const text = sel ? String(sel.toString() || '') : '';
        const page_number = detectSelectionPageNumber(sel, app.pdfViewer.currentPageNumber || 1);
        pyBridge.emit_selection_changed(text, JSON.stringify({ page_number, page_index: page_number - 1 }));
      }

      app.eventBus.on('pagechanging', (evt) => {
        pyBridge.emit_page_changed(Number(evt.pageNumber || app.pdfViewer.currentPageNumber || 1));
      });
      document.addEventListener('selectionchange', emitSelection);

      window.ohwPdfHost = {
        async openPdf(fileUrl, initialPage) {
          await app.open(String(fileUrl || ''));
          if (initialPage) {
            app.pdfViewer.currentPageNumber = Math.max(1, Number(initialPage));
          }
          annotations.renderAll();
        },
        goToPage(pageNumber) {
          app.pdfViewer.currentPageNumber = Math.max(1, Number(pageNumber || 1));
        },
        setZoom(modeOrValue) {
          if (typeof modeOrValue === 'number') app.pdfViewer.currentScale = modeOrValue;
          else app.pdfViewer.currentScaleValue = String(modeOrValue || 'page-width');
          annotations.renderAll();
        },
        setTool(tool) {
          const next = String(tool || 'select_text');
          annotations.setTool(next);
          const viewerContainer = document.getElementById('viewerContainer');
          if (viewerContainer) {
            viewerContainer.style.cursor = next === 'area' ? 'crosshair' : (next === 'erase' ? 'not-allowed' : 'auto');
          }
        },
        loadAnnotations(payload) {
          annotations.loadAnnotations(payload || []);
        },
        requestSelectedText() {
          emitSelection();
        },
        copySelectedText() {
          document.execCommand('copy');
        },
        clearSelection() {
          const sel = window.getSelection();
          if (sel) sel.removeAllRanges();
        },
        setPageMode(mode) {
          app.pdfViewer.scrollMode = String(mode || 'vertical') === 'single' ? 3 : 0;
        },
      };

      pyBridge.emit_viewer_ready(JSON.stringify({
        nativeTextSelection: true,
        nativeCopy: true,
        pageCoordinateAnnotations: true,
        tools: ['select_text', 'pan', 'area', 'erase'],
      }));
    });
  };
})();

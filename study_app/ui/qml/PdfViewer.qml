import QtQuick
import QtQuick.Controls
import QtQuick.Pdf

Item {
    id: root

    property string documentSource: ""
    property string interactionMode: "text_select"   // text_select | area_select | erase | pan
    property var overlayHighlights: []
    property string zoomMode: "fit_width"            // fit_width | fit_page | custom
    property real zoomFactor: 1.0
    property int currentPage: Math.max(1, pdfView.currentPage + 1)
    property real locationX: 0.0
    property real locationY: 0.0
    property string selectedText: (pdfView.selectedText || "")
    property bool singlePageMode: false

    property int pendingPage: 1
    property real pendingLocationX: 0.0
    property real pendingLocationY: 0.0

    PdfDocument {
        id: pdfDoc
        source: root.documentSource
    }

    function _clamp(v, lo, hi) {
        return Math.max(lo, Math.min(hi, v))
    }

    function _syncScale() {
        if (pdfDoc.pageCount <= 0) {
            return
        }
        if (root.zoomMode === "custom") {
            pdfView.renderScale = root.zoomFactor
            return
        }
        const pageIndex = _clamp(pdfView.currentPage, 0, Math.max(0, pdfDoc.pageCount - 1))
        const pagePt = pdfDoc.pagePointSize(pageIndex)
        if (!pagePt || pagePt.width <= 0 || pagePt.height <= 0) {
            return
        }
        const margin = 24
        const usableW = Math.max(20, width - margin)
        const usableH = Math.max(20, height - margin)
        const fitW = usableW / pagePt.width
        const fitH = usableH / pagePt.height
        if (root.zoomMode === "fit_page") {
            pdfView.renderScale = Math.max(0.1, Math.min(fitW, fitH))
        } else {
            pdfView.renderScale = Math.max(0.1, fitW)
        }
        root.zoomFactor = pdfView.renderScale
    }

    function _applyPendingJump() {
        if (pdfDoc.pageCount <= 0) {
            return
        }
        const pageZero = _clamp(root.pendingPage - 1, 0, Math.max(0, pdfDoc.pageCount - 1))
        pdfView.goToPage(pageZero)
        const maxScroll = Math.max(0, pdfView.contentHeight - pdfView.height)
        const normalizedY = _clamp(root.pendingLocationY, 0.0, 1.0)
        pdfView.contentY = maxScroll * normalizedY
        root.locationY = normalizedY
        root.locationX = 0.0
    }

    Rectangle {
        anchors.fill: parent
        color: "#111722"
    }

    PdfMultiPageView {
        id: pdfView
        anchors.fill: parent
        anchors.margins: 4
        document: pdfDoc
        pageSpacing: 10
        clip: true
        focus: true
        visible: !root.singlePageMode

        onCurrentPageChanged: {
            root.currentPage = Math.max(1, currentPage + 1)
            viewerBridge.emitPageChanged(root.currentPage, root.locationX, root.locationY)
            root._syncScale()
            overlayCanvas.requestPaint()
        }

        onContentYChanged: {
            const maxScroll = Math.max(1.0, contentHeight - height)
            root.locationY = root._clamp(contentY / maxScroll, 0.0, 1.0)
            viewerBridge.emitPageChanged(root.currentPage, root.locationX, root.locationY)
        }

        onSelectedTextChanged: {
            root.selectedText = selectedText || ""
        }

        MouseArea {
            id: modeMouse
            anchors.fill: parent
            enabled: root.interactionMode === "area_select" || root.interactionMode === "erase"
            acceptedButtons: Qt.LeftButton
            cursorShape: root.interactionMode === "erase" ? Qt.CrossCursor : Qt.CrossCursor
            preventStealing: true
            hoverEnabled: true
            z: 15

            property real dragStartX: 0
            property real dragStartY: 0
            property real dragEndX: 0
            property real dragEndY: 0
            property bool dragging: false

            onPressed: function(mouse) {
                if (root.interactionMode === "area_select") {
                    dragStartX = mouse.x
                    dragStartY = mouse.y
                    dragEndX = mouse.x
                    dragEndY = mouse.y
                    dragging = true
                    overlayCanvas.requestPaint()
                    return
                }
                if (root.interactionMode === "erase") {
                    const nx = root._clamp(mouse.x / width, 0.0, 1.0)
                    const ny = root._clamp(mouse.y / height, 0.0, 1.0)
                    for (let i = root.overlayHighlights.length - 1; i >= 0; --i) {
                        const h = root.overlayHighlights[i]
                        const rects = h.rects || []
                        for (let r = 0; r < rects.length; ++r) {
                            const rr = rects[r]
                            if (nx >= rr.x && ny >= rr.y && nx <= (rr.x + rr.w) && ny <= (rr.y + rr.h)) {
                                viewerBridge.emitHighlightHit(parseInt(h.id || 0))
                                return
                            }
                        }
                    }
                }
            }

            onPositionChanged: function(mouse) {
                if (!dragging || root.interactionMode !== "area_select") {
                    return
                }
                dragEndX = mouse.x
                dragEndY = mouse.y
                overlayCanvas.requestPaint()
            }

            onReleased: function(mouse) {
                if (!dragging || root.interactionMode !== "area_select") {
                    return
                }
                dragging = false
                dragEndX = mouse.x
                dragEndY = mouse.y
                const x1 = Math.min(dragStartX, dragEndX)
                const y1 = Math.min(dragStartY, dragEndY)
                const x2 = Math.max(dragStartX, dragEndX)
                const y2 = Math.max(dragStartY, dragEndY)
                overlayCanvas.requestPaint()
                if ((x2 - x1) < 6 || (y2 - y1) < 6) {
                    return
                }
                viewerBridge.emitAreaRectCreated({
                    "x": Number((x1 / width).toFixed(6)),
                    "y": Number((y1 / height).toFixed(6)),
                    "w": Number(((x2 - x1) / width).toFixed(6)),
                    "h": Number(((y2 - y1) / height).toFixed(6)),
                }, root.currentPage)
            }
        }

        TapHandler {
            acceptedButtons: Qt.RightButton
            onTapped: function(eventPoint) {
                const selected = (pdfView.selectedText || "").trim()
                if (!selected) {
                    return
                }
                viewerBridge.emitSelectionMenuRequested(
                    eventPoint.position.x,
                    eventPoint.position.y,
                    selected,
                    root.currentPage,
                )
            }
        }
    }

    PdfPageView {
        id: singlePageView
        anchors.fill: parent
        anchors.margins: 4
        document: pdfDoc
        currentPage: Math.max(0, root.currentPage - 1)
        renderScale: pdfView.renderScale
        visible: root.singlePageMode
    }

    Canvas {
        id: overlayCanvas
        anchors.fill: parent
        z: 20

        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()

            for (let i = 0; i < root.overlayHighlights.length; ++i) {
                const h = root.overlayHighlights[i]
                const color = h.color || "#2d9cdb"
                const opacity = Math.max(0.1, Math.min(1.0, Number(h.opacity || 0.35)))
                const rects = h.rects || []

                for (let r = 0; r < rects.length; ++r) {
                    const rr = rects[r]
                    const x = rr.x * width
                    const y = rr.y * height
                    const w = rr.w * width
                    const hpx = rr.h * height
                    ctx.fillStyle = Qt.rgba(Qt.color(color).r, Qt.color(color).g, Qt.color(color).b, opacity)
                    ctx.strokeStyle = color
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.roundedRect(x, y, w, hpx, 3, 3)
                    ctx.fill()
                    ctx.stroke()
                }
            }

            if (modeMouse.enabled && modeMouse.dragging && root.interactionMode === "area_select") {
                const x = Math.min(modeMouse.dragStartX, modeMouse.dragEndX)
                const y = Math.min(modeMouse.dragStartY, modeMouse.dragEndY)
                const w = Math.abs(modeMouse.dragEndX - modeMouse.dragStartX)
                const h = Math.abs(modeMouse.dragEndY - modeMouse.dragStartY)
                ctx.fillStyle = "#402d9cdb"
                ctx.strokeStyle = "#7ecbff"
                ctx.setLineDash([6, 4])
                ctx.lineWidth = 2
                ctx.beginPath()
                ctx.roundedRect(x, y, w, h, 2, 2)
                ctx.fill()
                ctx.stroke()
            }
        }
    }

    onDocumentSourceChanged: {
        root.currentPage = 1
        root.selectedText = ""
    }

    onOverlayHighlightsChanged: overlayCanvas.requestPaint()
    onWidthChanged: root._syncScale()
    onHeightChanged: root._syncScale()
    onZoomModeChanged: root._syncScale()
    onZoomFactorChanged: {
        if (zoomMode === "custom") {
            pdfView.renderScale = zoomFactor
        }
    }
    onPendingPageChanged: Qt.callLater(root._applyPendingJump)
    onPendingLocationYChanged: Qt.callLater(root._applyPendingJump)

    Connections {
        target: pdfDoc
        function onStatusChanged() {
            if (pdfDoc.status === PdfDocument.Ready) {
                Qt.callLater(root._syncScale)
                Qt.callLater(root._applyPendingJump)
            }
        }
    }
}

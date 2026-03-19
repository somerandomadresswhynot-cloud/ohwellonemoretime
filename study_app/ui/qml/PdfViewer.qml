import QtQuick
import QtQuick.Pdf

Item {
    id: root

    property string documentSource: ""
    property string selectedText: (pdfView.selectedText || "")
    property int currentPage: Math.max(1, pdfView.currentPage + 1)
    property real zoomFactor: pdfView.renderScale

    PdfDocument {
        id: pdfDoc
        source: root.documentSource
    }

    PdfMultiPageView {
        id: pdfView
        anchors.fill: parent
        document: pdfDoc
    }

    function jumpToPage(page) {
        pdfView.goToPage(Math.max(0, page - 1))
    }

    function jumpToLocation(page, x, y, zoom) {
        pdfView.goToLocation(Math.max(0, page - 1), Qt.point(x, y), zoom)
    }

    function fitToWidth() {
        pdfView.scaleToWidth(width, height)
    }

    function fitToPage() {
        pdfView.scaleToPage(width, height)
    }

    function resetScale() {
        pdfView.resetScale()
    }

    function setRenderScale(scale) {
        pdfView.renderScale = scale
    }

    function copySelection() {
        pdfView.copySelectionToClipboard()
    }

    function selectAllText() {
        pdfView.selectAll()
    }
}

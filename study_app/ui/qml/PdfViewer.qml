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

    Rectangle {
        anchors.fill: parent
        color: "#1b2431"
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 10
        color: "#202b3a"
        border.color: "#2a374a"
        border.width: 1
        radius: 4
    }

    PdfMultiPageView {
        id: pdfView
        anchors.fill: parent
        anchors.margins: 14
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

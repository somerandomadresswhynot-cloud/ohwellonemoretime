import os
import unittest


class PdfViewerSmokeTests(unittest.TestCase):
    @unittest.skipIf(os.environ.get('CI') == '1', 'UI smoke test is best-effort and skipped in CI')
    def test_pdf_viewer_wrapper_legacy_methods_do_not_crash(self):
        try:
            from PySide6.QtWidgets import QApplication
            from study_app.ui.embedded_pdf_viewer import EmbeddedPdfViewer
        except Exception:
            self.skipTest('PySide6 unavailable in this environment')

        app = QApplication.instance() or QApplication([])
        viewer = EmbeddedPdfViewer()

        # Legacy API remains callable as no-ops after removing custom overlay architecture.
        viewer.set_area_created_handler(lambda rect, page: None)
        viewer.set_highlight_hit_handler(lambda hid: None)
        viewer.set_annotation_tool('select_text')
        viewer.set_annotation_tool('area')
        viewer.set_annotation_tool('pan')
        viewer.set_annotation_tool('erase')
        viewer.set_overlay_highlights([
            {'id': 1, 'color': '#2d9cdb', 'opacity': 0.4, 'rects': [{'x': 0.1, 'y': 0.1, 'w': 0.2, 'h': 0.1}]},
        ])

        state = viewer.view_state()
        self.assertIn('page', state)
        self.assertIn('location', state)
        self.assertGreaterEqual(int(state['page']), 1)

        viewer.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()

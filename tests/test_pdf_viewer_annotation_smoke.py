import os
import unittest


class PdfViewerSmokeTests(unittest.TestCase):
    @unittest.skipIf(os.environ.get('CI') == '1', 'UI smoke test is best-effort and skipped in CI')
    def test_pdf_viewer_basic_controls_do_not_crash(self):
        try:
            from PySide6.QtWidgets import QApplication
            from study_app.ui.pdf_viewer import PersistentPdfViewer
        except Exception:
            self.skipTest('PySide6 or Qt WebEngine unavailable in this environment')

        app = QApplication.instance() or QApplication([])
        viewer = PersistentPdfViewer()

        viewer.set_zoom(1.2)
        viewer.zoom_in()
        viewer.zoom_out()
        viewer.reset_zoom()
        viewer.set_fit_mode()
        viewer.set_fit_page_mode()
        viewer.reload_pdf()

        state = viewer.view_state()
        self.assertIn('page', state)
        self.assertIn('location', state)
        self.assertGreaterEqual(int(state['page']), 1)

        viewer.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()

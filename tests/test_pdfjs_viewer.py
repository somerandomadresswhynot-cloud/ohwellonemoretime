import os
import unittest

try:
    from study_app.ui.pdf_stock_viewer import PdfStockBridge, normalize_annotation_rect, project_page_rect_to_viewport
    _IMPORT_ERR = None
except Exception as exc:
    PdfStockBridge = None
    normalize_annotation_rect = None
    project_page_rect_to_viewport = None
    _IMPORT_ERR = exc


@unittest.skipIf(_IMPORT_ERR is not None, f'PySide6/pdfjs viewer unavailable: {_IMPORT_ERR}')
class PdfJsBridgeTests(unittest.TestCase):
    def test_bridge_selected_text_callback(self):
        bridge = PdfStockBridge()
        seen = {}
        bridge.selection_changed.connect(lambda txt, info: seen.update({"txt": txt, "info": info}))
        bridge.emit_selection_changed("hello", '{"page_number":4,"page_index":3}')
        self.assertEqual(seen["txt"], "hello")
        self.assertEqual(int(seen["info"]["page_number"]), 4)


@unittest.skipIf(_IMPORT_ERR is not None, f'PySide6/pdfjs viewer unavailable: {_IMPORT_ERR}')
class PdfJsAnnotationSerializationTests(unittest.TestCase):
    def test_rect_serialization_deserialization(self):
        legacy = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
        current = {"x": 10, "y": 20, "width": 30, "height": 40, "coord_space": "page"}
        n1 = normalize_annotation_rect(legacy)
        n2 = normalize_annotation_rect(current)
        self.assertEqual(n1["coord_space"], "normalized")
        self.assertEqual(n2["coord_space"], "page")
        self.assertAlmostEqual(n1["width"], 0.3, places=6)
        self.assertAlmostEqual(n2["height"], 40.0, places=6)

    def test_coordinate_transform_page_space(self):
        rect = {"x": 100, "y": 200, "width": 50, "height": 80, "coord_space": "page"}
        out = project_page_rect_to_viewport(rect, page_size=(1000, 2000), viewport_size=(500, 1000))
        self.assertAlmostEqual(out["left"], 50.0, places=6)
        self.assertAlmostEqual(out["width"], 25.0, places=6)
        self.assertAlmostEqual(out["top"], 860.0, places=6)


class PdfJsViewerSmokeTests(unittest.TestCase):
    @unittest.skipIf(os.environ.get('CI') == '1', 'UI smoke test is best-effort and skipped in CI')
    def test_widget_loads(self):
        try:
            from PySide6.QtWidgets import QApplication
            from study_app.ui.pdf_stock_viewer import PersistentPdfViewer
        except Exception:
            self.skipTest('PySide6 unavailable in this environment')

        app = QApplication.instance() or QApplication([])
        viewer = PersistentPdfViewer()
        self.assertTrue(hasattr(viewer, "prime_path"))
        viewer.prime_path("")
        viewer.set_annotation_tool('select_text')
        viewer.set_overlay_highlights([
            {'id': 1, 'color': '#2d9cdb', 'opacity': 0.4, 'rects': [{'x': 10, 'y': 10, 'width': 20, 'height': 15}]},
        ])
        state = viewer.view_state()
        self.assertIn('page', state)
        self.assertGreaterEqual(int(state['page']), 1)
        viewer.deleteLater()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()

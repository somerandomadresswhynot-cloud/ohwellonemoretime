import unittest
from unittest.mock import patch

from study_app.pdf.pdf_service import PdfService


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, _path: str):
        self.pages = [_FakePage(''), _FakePage('hello world'), _FakePage('')]


class PdfServiceProbeTests(unittest.TestCase):
    def test_probe_detects_text_layer(self):
        svc = PdfService()
        with patch('study_app.pdf.pdf_service.PdfReader', _FakeReader):
            probe = svc.probe_text_layer('/tmp/whatever.pdf', pages_to_sample=3)
        self.assertTrue(probe['has_text_layer'])
        self.assertEqual(probe['sampled_pages'], 3)
        self.assertEqual(probe['text_pages'], 1)

    def test_probe_caches_results(self):
        svc = PdfService()
        calls = {'n': 0}

        class _CountingReader(_FakeReader):
            def __init__(self, path: str):
                calls['n'] += 1
                super().__init__(path)

        with patch('study_app.pdf.pdf_service.PdfReader', _CountingReader):
            a = svc.probe_text_layer('/tmp/same.pdf')
            b = svc.probe_text_layer('/tmp/same.pdf')
        self.assertEqual(calls['n'], 1)
        self.assertEqual(a, b)

    def test_extract_page_range_text_normalizes_breaks(self):
        svc = PdfService()

        class _Reader:
            def __init__(self, _path: str):
                self.pages = [
                    _FakePage("Hello wor-\nld\n\nNext line"),
                    _FakePage("second page\nstarts"),
                ]

        with patch('study_app.pdf.pdf_service.PdfReader', _Reader):
            out = svc.extract_page_range_text('/tmp/a.pdf', 1, 2)
        self.assertIn("Hello world", out)
        self.assertIn("Next line", out)
        self.assertIn("second page starts", out)

    def test_extract_page_text_single_page(self):
        svc = PdfService()

        class _Reader:
            def __init__(self, _path: str):
                self.pages = [_FakePage("one"), _FakePage("two")]

        with patch('study_app.pdf.pdf_service.PdfReader', _Reader):
            out = svc.extract_page_text('/tmp/a.pdf', 2)
        self.assertEqual(out, "two")


if __name__ == '__main__':
    unittest.main()

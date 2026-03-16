import unittest

from study_app.services.outline_service import entries_from_bookmarks, parse_outline_text


class OutlineLeafUnitTests(unittest.TestCase):
    def test_parse_outline_marks_only_leaf_ranges_as_units(self):
        text = """
# Book
## Chapter 1 [p43-83]
### Topic A [p45-50]
### Topic B [p51-60]
## Chapter 2 [p84-100]
""".strip()
        entries, errs = parse_outline_text(text)
        self.assertEqual(errs, [])
        by_title = {e["title"]: e for e in entries}
        self.assertFalse(by_title["Book"]["is_unit"])
        self.assertFalse(by_title["Chapter 1"]["is_unit"])
        self.assertTrue(by_title["Topic A"]["is_unit"])
        self.assertTrue(by_title["Topic B"]["is_unit"])
        self.assertTrue(by_title["Chapter 2"]["is_unit"])

    def test_bookmarks_mark_parent_ranges_non_units(self):
        bookmarks = [
            (1, "Chapter 1", 43),
            (2, "Topic A", 45),
            (2, "Topic B", 51),
            (1, "Chapter 2", 84),
        ]
        entries = entries_from_bookmarks(bookmarks, page_count=120, root_title="Book")
        by_title = {e["title"]: e for e in entries}
        self.assertFalse(by_title["Chapter 1"]["is_unit"])
        self.assertTrue(by_title["Topic A"]["is_unit"])
        self.assertTrue(by_title["Topic B"]["is_unit"])
        self.assertTrue(by_title["Chapter 2"]["is_unit"])


if __name__ == "__main__":
    unittest.main()

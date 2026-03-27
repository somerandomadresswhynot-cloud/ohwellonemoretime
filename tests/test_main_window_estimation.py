import unittest

from study_app.domain.models import UnitView

try:
    from study_app.ui.main_window import SourcesPage
except Exception:  # pragma: no cover - environment may miss PySide6
    SourcesPage = None


class _SettingsRepo:
    def get(self, key: str, default: str = "") -> str:
        return default


class _ReviewRepo:
    def avg_elapsed_seconds_for_unit(self, _unit_id: int):
        return None

    def avg_elapsed_seconds_for_source(self, _source_id: int):
        return None

    def avg_elapsed_seconds_global(self):
        return None


class _DummySourcesPage:
    source_id = 1
    settings_repo = _SettingsRepo()
    review_repo = _ReviewRepo()


class EstimateReviewSecondsTests(unittest.TestCase):
    @unittest.skipIf(SourcesPage is None, "PySide6/main_window unavailable in this environment")
    def test_accepts_unit_view_dataclass(self):
        page = _DummySourcesPage()
        row = UnitView(
            unit_id=42,
            node_id=1,
            source_id=1,
            source_title="Book",
            title="Unit",
            hierarchy_path="Book › Unit",
            start_page=10,
            end_page=12,
            queue_enabled=True,
            next_review_at=None,
            last_review_at=None,
            review_count=0,
            avg_rating=0.0,
        )
        seconds = SourcesPage._estimate_review_seconds_unit_row(page, row)
        self.assertGreater(seconds, 0.0)


if __name__ == "__main__":
    unittest.main()

import pytest

pytest.importorskip("PySide6")

from study_app.domain.models import UnitView
from study_app.ui.main_window import SourceWorkspace


class _SettingsStub:
    def get(self, key: str, default: str = "") -> str:
        values = {
            "fallback_review_seconds_per_page": "60",
            "fallback_review_seconds_per_unit": "90",
        }
        return values.get(key, default)


class _ReviewRepoStub:
    def __init__(self):
        self.last_unit_id = None

    def avg_elapsed_seconds_for_unit(self, unit_id: int):
        self.last_unit_id = unit_id
        return None

    def avg_elapsed_seconds_for_source(self, source_id: int):
        return None

    def avg_elapsed_seconds_global(self):
        return None


def test_estimate_review_seconds_accepts_unitview_rows():
    ws = SourceWorkspace.__new__(SourceWorkspace)
    ws.settings_repo = _SettingsStub()
    ws.review_repo = _ReviewRepoStub()
    ws.source_id = 5

    row = UnitView(
        unit_id=42,
        node_id=1,
        source_id=5,
        source_title="S",
        title="U",
        hierarchy_path="U",
        start_page=3,
        end_page=5,
        queue_enabled=True,
        next_review_at=None,
        last_review_at="2025-01-01T00:00:00+00:00",
        review_count=2,
        avg_rating=3.0,
    )

    seconds = ws._estimate_review_seconds_unit_row(row)

    assert seconds == 180.0
    assert ws.review_repo.last_unit_id == 42

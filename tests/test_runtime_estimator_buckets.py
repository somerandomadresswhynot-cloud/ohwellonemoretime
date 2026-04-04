from __future__ import annotations

import tempfile

from study_app.persistence.database import Database
from study_app.persistence.repositories import (
    EVENT_AGAIN,
    EVENT_LEARNING_SUBMIT,
    OutlineRepo,
    ReviewRepo,
    SourceRepo,
    TIME_BUCKET_FIRST_ENCOUNTER,
    TIME_BUCKET_LEARNING_REPEAT,
    TIME_BUCKET_MATURE_REVIEW,
    TIME_BUCKET_STABILIZING_RECALL,
    classify_event_time_bucket,
    derive_next_session_bucket,
)
from study_app.services.queue_planner import plan_session_queue
from study_app.services.runtime_estimator import build_runtime_estimation_model


def _mk_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db")
    db = Database(tmp.name)
    return tmp, db


def _seed_source_with_units(db: Database, title: str, spans: list[tuple[int, int]]) -> tuple[ReviewRepo, int, list[int]]:
    source_repo = SourceRepo(db)
    outline_repo = OutlineRepo(db)
    review_repo = ReviewRepo(db)
    source_id = source_repo.create(title, f"/tmp/{title}.pdf", 1, 300)
    entries = [{"depth": 1, "title": title, "start_page": None, "end_page": None, "is_unit": False, "queue_enabled": True}]
    for i, (sp, ep) in enumerate(spans, start=1):
        entries.append({"depth": 2, "title": f"U{i}", "start_page": sp, "end_page": ep, "is_unit": True, "queue_enabled": True})
    outline_repo.replace_outline(source_id, entries)
    units = [int(r["id"]) for r in review_repo.source_units(source_id)]
    return review_repo, source_id, units


def _payload(at: str, elapsed: int, rating: str = "easy"):
    return {
        "started_at": at,
        "ended_at": at,
        "elapsed_seconds": elapsed,
        "rating": rating,
        "pre_note": "",
        "post_note": "",
    }


def _stats(at: str):
    return {
        "last_review_at": at,
        "review_count": 1,
        "ease_factor": 2.5,
        "avg_rating": 5.0,
        "fsrs_difficulty": 4.5,
        "fsrs_stability": 3.0,
        "fsrs_last_review_at": at,
        "fsrs_last_grade": 4,
        "fsrs_review_count": 1,
        "fsrs_lapse_count": 0,
        "fsrs_state_version": 1,
        "fsrs_due_retention_used": 0.9,
    }


def test_bucket_classifier_distinguishes_first_encounter_vs_learning_repeat():
    first = {"event_kind": EVENT_LEARNING_SUBMIT, "rating": EVENT_LEARNING_SUBMIT}
    second = {"event_kind": EVENT_LEARNING_SUBMIT, "rating": EVENT_LEARNING_SUBMIT}
    assert classify_event_time_bucket(first, []) == TIME_BUCKET_FIRST_ENCOUNTER
    assert classify_event_time_bucket(second, [first]) == TIME_BUCKET_LEARNING_REPEAT


def test_projected_bucket_for_empty_and_learning_histories():
    assert derive_next_session_bucket([]) == TIME_BUCKET_FIRST_ENCOUNTER
    events = [{"event_kind": EVENT_LEARNING_SUBMIT, "ended_at": "2026-01-01T00:00:00+00:00", "id": 1, "rating": EVENT_LEARNING_SUBMIT}]
    assert derive_next_session_bucket(events) == TIME_BUCKET_LEARNING_REPEAT


def test_source_local_median_is_used_when_reliable():
    tmp, db = _mk_db()
    try:
        repo, source_id, units = _seed_source_with_units(db, "S1", [(1, 2), (3, 4), (5, 6)])
        for idx, uid in enumerate(units):
            repo.record_learning_event(uid, _payload(f"2026-03-0{idx+1}T09:00:00+00:00", elapsed=120 + (idx * 60)), EVENT_LEARNING_SUBMIT)
        model = build_runtime_estimation_model(repo.runtime_estimation_observations(), 60.0, 90.0)
        new_unit = type("U", (), {"source_id": source_id, "start_page": 10, "end_page": 11})()
        details = model.estimate_unit_seconds_with_details(new_unit, TIME_BUCKET_FIRST_ENCOUNTER, source_id=source_id)
        assert details["provenance"] == "source:first_encounter"
        # median of [60, 90, 120] sec/page for 2-page units => 90 sec/page * 2 pages
        assert abs(details["seconds"] - 180.0) < 1e-6
    finally:
        db.close()
        tmp.close()


def test_sparse_source_falls_back_to_global_bucket():
    tmp, db = _mk_db()
    try:
        repo, source_a, units_a = _seed_source_with_units(db, "A", [(1, 2), (3, 4), (5, 6)])
        for idx, uid in enumerate(units_a):
            repo.record_learning_event(uid, _payload(f"2026-03-1{idx}T09:00:00+00:00", elapsed=120), EVENT_LEARNING_SUBMIT)

        repo_b, source_b, units_b = _seed_source_with_units(db, "B", [(20, 21)])
        repo_b.record_learning_event(units_b[0], _payload("2026-03-20T09:00:00+00:00", elapsed=600), EVENT_LEARNING_SUBMIT)

        model = build_runtime_estimation_model(repo.runtime_estimation_observations(), 60.0, 90.0)
        unit = type("U", (), {"source_id": source_b, "start_page": 30, "end_page": 31})()
        details = model.estimate_unit_seconds_with_details(unit, TIME_BUCKET_FIRST_ENCOUNTER, source_id=source_b)
        assert details["provenance"] == "global:first_encounter"
        assert abs(details["seconds"] - 120.0) < 1e-6
    finally:
        db.close()
        tmp.close()


def test_adjacent_bucket_and_default_fallbacks():
    observations = [
        {
            "event_id": 1,
            "unit_id": 1,
            "source_id": 1,
            "ended_at": "2026-03-01T10:00:00+00:00",
            "elapsed_seconds": 120,
            "rating": EVENT_AGAIN,
            "event_kind": EVENT_AGAIN,
            "fsrs_grade": None,
            "page_count": 2,
        }
    ]
    model = build_runtime_estimation_model(observations, 60.0, 90.0)
    unit = type("U", (), {"source_id": 1, "start_page": 1, "end_page": 2})()
    mature = model.estimate_unit_seconds_with_details(unit, TIME_BUCKET_MATURE_REVIEW, source_id=1)
    assert mature["provenance"] == "global:stabilizing_recall"

    empty_model = build_runtime_estimation_model([], 60.0, 90.0)
    defaulted = empty_model.estimate_unit_seconds_with_details(unit, TIME_BUCKET_FIRST_ENCOUNTER, source_id=1)
    assert defaulted["provenance"] == "default"
    assert abs(defaulted["seconds_per_page"] - 81.0) < 1e-6


def test_queue_planner_is_bounded_by_projected_time_estimate():
    due_units = ["u1", "u2", "u3"]
    estimates = {"u1": 120.0, "u2": 120.0, "u3": 120.0}
    plan = plan_session_queue(due_units, available_minutes=5, estimate_seconds=lambda u: estimates[u])
    assert plan.selected_units == ["u1", "u2"]
    assert plan.overflow_count == 1

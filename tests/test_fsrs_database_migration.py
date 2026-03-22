import tempfile

from study_app.persistence.database import Database


def test_units_table_has_nullable_fsrs_columns():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            cols = {r['name'] for r in db.conn.execute('PRAGMA table_info(units)').fetchall()}
            assert 'fsrs_difficulty' in cols
            assert 'fsrs_stability' in cols
            assert 'fsrs_last_review_at' in cols
            assert 'fsrs_last_grade' in cols
            assert 'fsrs_review_count' in cols
            assert 'fsrs_lapse_count' in cols
            assert 'fsrs_state_version' in cols
            assert 'fsrs_due_retention_used' in cols
            assert 'fsrs_parameters_json' in cols
        finally:
            db.close()


def test_insert_unit_without_fsrs_values_still_works():
    with tempfile.NamedTemporaryFile(suffix='.db') as tmp:
        db = Database(tmp.name)
        try:
            now = '2026-03-22T00:00:00+00:00'
            db.conn.execute(
                "INSERT INTO sources(title,file_path,file_size,page_count,is_active,learning_mode,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ('Book', '/tmp/book.pdf', 0, 10, 1, 'any', now, now),
            )
            source_id = int(db.conn.execute('SELECT id FROM sources').fetchone()['id'])
            db.conn.execute(
                "INSERT INTO outline_nodes(source_id,parent_id,title,depth,order_index,start_page,end_page,is_unit,queue_enabled) VALUES(?,?,?,?,?,?,?,?,?)",
                (source_id, None, 'U1', 1, 0, 1, 2, 1, 1),
            )
            node_id = int(db.conn.execute('SELECT id FROM outline_nodes').fetchone()['id'])
            db.conn.execute(
                "INSERT INTO units(source_id,node_id,title,start_page,end_page,queue_enabled,last_review_at,next_review_at,review_count,ease_factor,interval_days,avg_rating) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_id, node_id, 'U1', 1, 2, 1, None, now, 0, 2.5, 0.0, 0.0),
            )
            db.conn.commit()
            row = db.conn.execute('SELECT * FROM units').fetchone()
            assert row['fsrs_difficulty'] is None
            assert row['fsrs_state_version'] is None
        finally:
            db.close()

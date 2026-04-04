from study_app.services.cloze_utils import replace_nth_cloze


def test_replace_nth_cloze_targets_exact_occurrence_with_identical_values():
    text = 'A {{c::same}} B {{c::same}} C {{c::same}}'
    assert replace_nth_cloze(text, 1) == 'A {{c::same}} B same C {{c::same}}'
    assert replace_nth_cloze(text, 0) == 'A same B {{c::same}} C {{c::same}}'
    assert replace_nth_cloze(text, 2) == 'A {{c::same}} B {{c::same}} C same'


def test_replace_nth_cloze_out_of_bounds_is_noop():
    text = 'X {{c::one}} Y'
    assert replace_nth_cloze(text, 99) == text
    assert replace_nth_cloze(text, -1) == text

"""Tests for glossary-based post-correction."""

from meetingnotes.correct import apply_corrections, correct_segments


def test_empty_corrections_is_noop():
    assert apply_corrections("hello world", {}) == "hello world"


def test_simple_replacement_case_insensitive():
    corr = {"утилект": "Utilitect"}
    assert apply_corrections("мы используем утилект здесь", corr) == "мы используем Utilitect здесь"


def test_word_boundary_respected():
    corr = {"мес": "MES"}
    # "мес" inside "место" must NOT be replaced
    assert apply_corrections("место для мес системы", corr) == "место для MES системы"


def test_multiword_phrase_takes_precedence():
    corr = {"шоу": "show", "шоу месседж": "showMessage"}
    out = apply_corrections("вызываем шоу месседж и просто шоу", corr)
    assert "showMessage" in out
    assert "show месседж" not in out


def test_correct_segments_does_not_mutate_input():
    segments = [{"start": 0.0, "end": 1.0, "text": "утилект"}]
    result = correct_segments(segments, {"утилект": "Utilitect"})
    assert result[0]["text"] == "Utilitect"
    assert segments[0]["text"] == "утилект"  # original untouched


def test_correct_segments_preserves_other_fields():
    segments = [{"start": 1.5, "end": 2.5, "text": "тест"}]
    result = correct_segments(segments, {})
    assert result[0]["start"] == 1.5
    assert result[0]["end"] == 2.5


"""Tests for the WER computation."""

import pytest

from meetingnotes.wer import compute_wer, normalize


def test_perfect_match_zero_wer():
    r = compute_wer("привет как дела", "привет как дела")
    assert r.wer == 0.0
    assert r.accuracy == 1.0


def test_case_and_punctuation_normalized():
    r = compute_wer("Привет, как дела!", "привет как дела")
    assert r.wer == 0.0


def test_one_substitution():
    r = compute_wer("нам нужно смог тесты", "нам нужно smoke тесты")
    # 1 substitution over 4 words
    assert r.substitutions == 1
    assert r.wer == pytest.approx(1 / 4)


def test_insertion_and_deletion():
    r = compute_wer("создаем приложение", "создаем новое приложение здесь удалено")
    assert (r.insertions + r.deletions) >= 1


def test_normalize_strips_punctuation():
    assert normalize("Привет, мир! Как: дела?") == ["привет", "мир", "как", "дела"]


def test_empty_reference_raises():
    with pytest.raises(ValueError):
        compute_wer("", "что-то")


def test_wer_can_exceed_one_for_insertions():
    r = compute_wer("коротко", "коротко но с очень длинным добавлением слов")
    assert r.wer > 1.0

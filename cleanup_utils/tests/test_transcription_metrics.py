import pytest

from instagram_extractor.transcription_metrics import normalize_text, score_transcript, tokenize


def test_normalization_ignores_formatting_and_preserves_words():
    assert normalize_text("  Hello, WORLD!\nNew—line. ") == "hello world new line"


def test_rouge_l_exposes_missing_and_extra_words():
    missing = score_transcript("one two three four", "one two four", "en")
    assert missing.rouge_l_precision == pytest.approx(1.0)
    assert missing.rouge_l_recall == pytest.approx(0.75)
    assert missing.rouge_l_f1 == pytest.approx(6 / 7)
    assert missing.jaccard == pytest.approx(0.75)

    extra = score_transcript("one two", "one two invented", "en")
    assert extra.rouge_l_precision == pytest.approx(2 / 3)
    assert extra.rouge_l_recall == pytest.approx(1.0)


def test_empty_transcripts_have_defined_scores():
    assert score_transcript("", "", "en").rouge_l_f1 == 1.0
    assert score_transcript("speech", "", "en").rouge_l_recall == 0.0
    assert score_transcript("", "hallucination", "en").rouge_l_precision == 0.0


def test_mandarin_uses_word_segmentation_not_whitespace_only():
    tokens = tokenize("科学家希望了解行星如何形成。", "zh")
    assert len(tokens) > 1
    score = score_transcript("科学家希望了解行星如何形成", "科学家希望了解行星形成", "zh")
    assert 0 < score.rouge_l_recall < 1


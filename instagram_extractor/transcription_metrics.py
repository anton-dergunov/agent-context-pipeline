"""Formatting-tolerant multilingual transcript similarity metrics."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_CHINESE_LANGUAGES = {"zh", "cmn", "cmn_hans_cn"}


def normalize_text(text: str) -> str:
    """Normalize formatting without changing lexical content."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in text)
    return " ".join(text.split())


def tokenize(text: str, language: str) -> list[str]:
    """Return word tokens, using Jieba segmentation for Mandarin."""
    normalized = normalize_text(text)
    if not normalized:
        return []
    if language.casefold() in _CHINESE_LANGUAGES:
        import jieba

        jieba.setLogLevel(20)
        return [token for token in jieba.lcut(normalized) if token.strip()]
    return _WORD_RE.findall(normalized)


def _lcs_length(left: list[str], right: list[str]) -> int:
    """Length of the longest common subsequence using bounded memory."""
    if len(left) < len(right):
        left, right = right, left
    previous = [0] * (len(right) + 1)
    for left_token in left:
        current = [0]
        for index, right_token in enumerate(right, 1):
            if left_token == right_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


@dataclass(slots=True, frozen=True)
class SimilarityScore:
    rouge_l_precision: float
    rouge_l_recall: float
    rouge_l_f1: float
    jaccard: float
    reference_tokens: int
    candidate_tokens: int

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def score_transcript(reference: str, candidate: str, language: str) -> SimilarityScore:
    reference_tokens = tokenize(reference, language)
    candidate_tokens = tokenize(candidate, language)
    lcs = _lcs_length(reference_tokens, candidate_tokens)
    precision = lcs / len(candidate_tokens) if candidate_tokens else (1.0 if not reference_tokens else 0.0)
    recall = lcs / len(reference_tokens) if reference_tokens else (1.0 if not candidate_tokens else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    reference_set = set(reference_tokens)
    candidate_set = set(candidate_tokens)
    union = reference_set | candidate_set
    jaccard = len(reference_set & candidate_set) / len(union) if union else 1.0
    return SimilarityScore(
        rouge_l_precision=precision,
        rouge_l_recall=recall,
        rouge_l_f1=f1,
        jaccard=jaccard,
        reference_tokens=len(reference_tokens),
        candidate_tokens=len(candidate_tokens),
    )


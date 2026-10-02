"""Deduplicate OCR observations across image and video frames."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz.fuzz import ratio

from .models import OCRFrame, OCRLine, TextSegment


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _key(text: str) -> str:
    return re.sub(r"[^\w]+", "", normalize_text(text).casefold(), flags=re.UNICODE)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, bottom = max(ax, bx), max(ay, by)
    right, top = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    intersection = max(0.0, right - left) * max(0.0, top - bottom)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union else 0.0


def _same_evolving_text(existing: str, new: str) -> bool:
    a, b = _key(existing), _key(new)
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    if len(short) <= 2:
        return long.startswith(short)
    if long.startswith(short) or long.endswith(short):
        return True
    threshold = 67 if len(short) <= 4 else 72
    return ratio(a, b) >= threshold


def _prefer_text(a: str, b: str) -> str:
    """Prefer the fullest progressive string, otherwise the cleaner OCR candidate."""
    ak, bk = _key(a), _key(b)
    if ak and bk and (bk.startswith(ak) or bk.endswith(ak)) and len(bk) > len(ak):
        return normalize_text(b)
    if ak and bk and (ak.startswith(bk) or ak.endswith(bk)):
        return normalize_text(a)
    return normalize_text(b if len(bk) > len(ak) else a)


def _spatially_related(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    if _iou(a, b) >= 0.06:
        return True
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    a_center_y = ay + ah / 2
    b_center_y = by + bh / 2
    same_text_row = abs(a_center_y - b_center_y) <= max(ah, bh) * 1.15
    horizontal_overlap = min(ax + aw, bx + bw) - max(ax, bx)
    return same_text_row and horizontal_overlap >= min(aw, bw) * 0.08


@dataclass(slots=True)
class _Variant:
    text: str
    count: int = 0
    confidence_sum: float = 0.0
    best_confidence: float = 0.0

    def add(self, text: str, confidence: float) -> None:
        self.count += 1
        self.confidence_sum += confidence
        self.best_confidence = max(self.best_confidence, confidence)
        # Preserve the most complete formatting for an identical normalized key.
        self.text = _prefer_text(self.text, text)


@dataclass(slots=True)
class _Track:
    segment: TextSegment
    variants: dict[str, _Variant] = field(default_factory=dict)

    @classmethod
    def start(cls, text: str, line: OCRLine, time_seconds: float | None) -> "_Track":
        segment = TextSegment(
            text=text,
            confidence=line.confidence,
            first_seen_seconds=time_seconds,
            last_seen_seconds=time_seconds,
            bbox=line.bbox,
        )
        track = cls(segment=segment)
        track._add_variant(text, line.confidence)
        return track

    def _add_variant(self, text: str, confidence: float) -> None:
        key = _key(text)
        variant = self.variants.get(key)
        if variant is None:
            variant = _Variant(text=normalize_text(text))
            self.variants[key] = variant
        variant.add(text, confidence)

    def add(self, text: str, line: OCRLine, time_seconds: float | None) -> None:
        self._add_variant(text, line.confidence)
        self.segment.confidence = max(self.segment.confidence, line.confidence)
        self.segment.last_seen_seconds = time_seconds
        self.segment.bbox = line.bbox
        self.segment.observations += 1

    def finalize_text(self) -> None:
        variants = list(self.variants.values())
        keys = list(self.variants)
        # A typewriter/progressive reveal is a prefix/suffix chain. In that
        # special case the complete final string matters more than frequency.
        longest_key = max(keys, key=len)
        chain = len({len(key) for key in keys}) > 1 and all(
            longest_key.startswith(key) or longest_key.endswith(key) for key in keys
        )
        if chain:
            chosen = self.variants[longest_key]
        else:
            chosen = max(
                variants,
                key=lambda variant: (
                    variant.count,
                    variant.confidence_sum / max(1, variant.count),
                    len(_key(variant.text)),
                ),
            )
        self.segment.text = normalize_text(chosen.text)


def _merge_segment(target: TextSegment, source: TextSegment) -> None:
    target.text = _prefer_text(target.text, source.text)
    target.confidence = max(target.confidence, source.confidence)
    if target.first_seen_seconds is None or (
        source.first_seen_seconds is not None
        and source.first_seen_seconds < target.first_seen_seconds
    ):
        target.first_seen_seconds = source.first_seen_seconds
    if target.last_seen_seconds is None or (
        source.last_seen_seconds is not None and source.last_seen_seconds > target.last_seen_seconds
    ):
        target.last_seen_seconds = source.last_seen_seconds
        target.bbox = source.bbox
    target.observations += source.observations


def merge_frames(frames: list[OCRFrame], max_gap_seconds: float = 2.0) -> list[TextSegment]:
    """Merge repeated and progressively revealed OCR while retaining time ranges."""
    tracks: list[_Track] = []
    active: list[_Track] = []
    for frame in frames:
        if frame.time_seconds is not None:
            active = [
                track
                for track in active
                if track.segment.last_seen_seconds is None
                or frame.time_seconds - track.segment.last_seen_seconds <= max_gap_seconds
            ]
        for line in frame.lines:
            text = normalize_text(line.text)
            if not text:
                continue
            matches = [
                candidate
                for candidate in active
                if _spatially_related(candidate.segment.bbox, line.bbox)
                and any(
                    _same_evolving_text(variant.text, text)
                    for variant in candidate.variants.values()
                )
            ]
            match = max(
                matches,
                key=lambda candidate: max(
                    ratio(_key(variant.text), _key(text)) for variant in candidate.variants.values()
                ),
                default=None,
            )
            if match:
                match.add(text, line, frame.time_seconds)
            else:
                track = _Track.start(text, line, frame.time_seconds)
                tracks.append(track)
                active.append(track)

    for track in tracks:
        track.finalize_text()
    segments = [track.segment for track in tracks]

    # Collapse exact repeats across cuts while preserving first appearance order.
    collapsed: list[TextSegment] = []
    collapsed_by_key: dict[str, TextSegment] = {}
    for segment in segments:
        key = _key(segment.text)
        prior = collapsed_by_key.get(key)
        if prior:
            _merge_segment(prior, segment)
        else:
            # Repeated overlays can return after a cut with a minor OCR typo.
            fuzzy_prior = next(
                (
                    item
                    for item in collapsed
                    if _spatially_related(item.bbox, segment.bbox)
                    and min(len(_key(item.text)), len(key)) >= 4
                    and ratio(_key(item.text), key) >= 88
                ),
                None,
            )
            if fuzzy_prior:
                _merge_segment(fuzzy_prior, segment)
            else:
                collapsed.append(segment)
                collapsed_by_key[key] = segment
    return collapsed


def lines_to_frame(
    lines: list[OCRLine], time_seconds: float | None, frame_number: int | None
) -> OCRFrame:
    # OCR engines return individual lines; keep spatial reading order (top to bottom, left to right).
    ordered = sorted(lines, key=lambda line: (-(line.bbox[1] + line.bbox[3]), line.bbox[0]))
    return OCRFrame(time_seconds=time_seconds, frame_number=frame_number, lines=ordered)

"""The uniform output shape every extractor writes.

One filename convention across six handlers, so a reader — the pipeline, the
routing agent, or the user — learns it once. Everything an extractor retrieved
but did not convert lives under `raw/`, which is the single "do not open this"
rule that replaces a per-extractor list of patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CONTENT_NAME = "content.md"
COMMENTS_NAME = "comments.md"
METADATA_NAME = "metadata.json"
STATUS_NAME = "status.json"
RAW_DIR = "raw"

# The two names inside `raw/` that are fixed rather than per-extractor, because
# the index points a human at the first of them. A paper's PDF is always
# `raw/paper.pdf` whether it was the conversion source or a copy kept beside an
# HTML body, so finding the readable file never depends on which path the
# extraction happened to take.
PAPER_PDF_NAME = "paper.pdf"
PAPER_HTML_NAME = "paper.html"

# complete — the body was retrieved.
# partial  — some of it was, and `reason` says what is missing.
# blocked  — the source refused: paywall, login wall, 403, bot check.
# failed   — nothing was retrieved.
ExtractionStatus = Literal["complete", "partial", "blocked", "failed"]

EXTRACTION_STATUSES: tuple[str, ...] = ("complete", "partial", "blocked", "failed")

# Reasons that mean the source refused rather than that retrieval went wrong.
# The distinction matters downstream: a block is a fact about the source that
# routing must weigh, while a failure is a fact about this attempt.
BLOCKING_REASONS = frozenset({"access-blocked", "auth-wall", "login-required"})


def status_for_reason(reason: str | None) -> str:
    """Return the failing status a stable reason key belongs to."""
    return "blocked" if reason in BLOCKING_REASONS else "failed"


@dataclass(frozen=True, slots=True)
class HarvestedLink:
    """A link a finished extraction offers for one further round of retrieval.

    `via` names where inside the extraction the link was found — "author comment",
    "description" — so the item's index can say why a source it never received is
    part of it.
    """

    url: str
    via: str

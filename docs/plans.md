# Plans

What is not built yet. An entry leaves this file when it is built, with its reasoning folded into
the document for that topic, or when it is no longer wanted. Git keeps what it said.

## Capture

- **Ask for a note when an item arrives without one.** The owner's reason for saving something is the
  highest-value field in an item, and most bare links have none. Probably the largest remaining
  improvement to routing quality, and independent of everything else here.
- **OCR of Telegram photo attachments.** Short-form video earns its OCR because on-screen text is
  measurably where the content is. Whether a forwarded photo carries signal or noise is untested.
  The engine already exists in `src/info_triage/extractors/media/ocr.py` if the answer is yes. Evidence
  first.

## Server

- **Pruning SQLite.** The database is operational, not archival, and rows older than about a month
  could be removed. Nothing prunes it today.
- **Retrying a failed item.** An item in `failed` stays in staging until someone intervenes. A
  command that resets `failed` to `received` would let the normal loop try again.
- **Rotation for `processor-runs.jsonl`.** The log is append-only with no cleanup, deliberately so
  far.
- **A stronger deployment boundary.** Root-controlled Compose and Docker definitions, with the
  deployment account limited to application source; see
  [`operations/synology-deployment.md`](operations/synology-deployment.md#10-what-this-does-not-protect-against).

## Extraction

- **Throughput on the target hardware.** OCR and transcription were measured on a laptop. The NAS
  figure is an estimate; run `experiments/video-ocr/bench.py` there
  ([`experiments/video-ocr/`](../experiments/video-ocr/README.md)).
- **The original source of a Medium story.** A story cross-posted from a personal site could be
  retrieved there instead of through the member wall
  ([`experiments/medium-access/`](../experiments/medium-access/README.md)).
- **Grouping a long link list into themes.** The one place a language model might pay. Revisit only
  if resolved titles turn out not to be enough.

## Related notes

From [`experiments/related-notes/`](../experiments/related-notes/README.md):

- **Repeat the A/B runs.** Every comparison is one run per condition. The verdict patterns are stark
  enough to trust; the token percentages are not.
- **Re-fit the abstain threshold** once the inbox has breadth. It is fitted to 22 hand-judged
  queries on a sample dominated by one subject. The vault side will probably need a stricter cut
  than the plan side as the vault grows; re-measure when it has doubled.
- **Cache the block.** The pass costs about a minute. Keying each item's block on its revision and
  the two note repositories' current commits would make an unchanged sync cost seconds. In order of
  expected return after that: a smaller candidate pool, then shorter candidate text. Not ONNX,
  quantisation or threading.
- **A currency signal.** `published` is already in the frontmatter; items older than a threshold in
  a fast-moving field could be marked, so the search for a newer version runs on the few that need
  it.

## Measurement

- **Compare token cost on the finished contract.** The per-item budget in
  [`experiments/item-contract/`](../experiments/item-contract/README.md) is an estimate made before
  the contract existed. Re-run the same items against the current structure.

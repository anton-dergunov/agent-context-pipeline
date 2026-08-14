# Preprocessing Catalogue

This file is the authoritative catalogue of automatic preprocessing applied to
captured items. `config.yaml` enables processors by listing them under the
ordered `processing.steps` sequence.

## Current behaviour

| Telegram content | Automatic preprocessing | `capture/message.md` result |
| --- | --- | --- |
| Voice note (`voice`) | Transcribe locally, clean text, discover and resolve URLs | Transcript in its ordered `voice` segment |
| Plain text or caption | Clean text, discover and resolve URLs | Processed text in its ordered segment |
| Location or venue | Clean text, discover and resolve URLs | Existing readable location block in its segment |
| Document | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |
| Photo | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |
| Video | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |
| Animation | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |
| Generic audio (`audio`) | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |
| Video note (`video_note`) | Clean accompanying caption, then discover and resolve its URLs | Caption in its segment |

Original downloaded attachments and the complete Telegram payload are always
retained. Both Markdown files live under the item's `capture/` directory:
`source.md` contains the segmented text after source materialization (including
voice transcription) but before cleaning or URL resolution, and `message.md`
contains the transformed body with category front matter. The item's ordered
link table is written to `links.json` at the item root.

Cleaning runs before link discovery so that zero-width characters and homoglyphs
cannot hide a link from it. Discovery runs before URL resolution, which is the
only step that uses the network. Link destinations and titles inserted by
resolution are consequently not cleaned; canonicalizing them is link discovery's
job, not a second cleaning pass.

Every Telegram source message is represented uniformly:

```markdown
## Segment 1 — text

Personal commentary

## Segment 2 — text

https://example.com/shared-item
```

Kinds reflect known Telegram content such as `text`, `caption`, `voice`, or
`location`. `forwarded` is added only for explicit Telegram provenance; no
URL/length heuristic guesses which segment expresses personal intent.

Telegram sends a message as plain text plus a list of entities, and the plain
text alone drops the destination of every hyperlinked phrase. Segments therefore
render `text_link` entities back as ordinary Markdown links, so a forwarded post
whose text reads `Paper Github Video` keeps all three destinations in
`capture/source.md` and `capture/message.md`. Entity offsets are counted in
UTF-16 code units, and a label spanning a line break is left as plain text —
link discovery still records its destination. Other formatting entities such as
bold, italic, and code are deliberately ignored.

## Voice-note transcription

A voice item is detected from an attachment-manifest entry whose `kind` is
`voice`. Generic audio uploads, videos, and video notes do not trigger this
processor. Every downloaded voice attachment in a grouped item is transcribed
in attachment order.

The processor reuses the local transcription implementation used for Instagram
video audio. Backend, model, cache directory, and CPU threads come from the
`voice-transcription` entry in `config.yaml`. The shipped Synology configuration
uses its image-bundled multilingual `faster-whisper` small model with CPU/int8
inference. Runtime model downloads remain disabled in that container, so a
configured model change requires an image rebuild.

Language is detected automatically, speech stays in its original language,
voice-activity detection is enabled to suppress silence and music, and no
timestamps are added. A successful voice-only body is:

```markdown
## Segment 1 — voice

Recognized speech goes here.
```

In a grouped item, each transcript is rendered in the corresponding Telegram
message segment. Other text, captions, and locations remain in their own ordered
segments. A captioned voice message keeps the transcript and caption together in
its `voice` segment.

If Whisper detects no speech, the item is delivered with:

```markdown
## Segment 1 — voice

[No speech recognized]
```

A missing or unavailable attachment, an invalid media file, a file without an
audio stream, or a transcription error produces a declared failed processor
outcome. The voice step is rolled back, later text processors continue, and the
item is delivered with its original attachment so it can be played manually.
The processor failure and stable reason are visible on the Processors dashboard
tab and in the append-only processor log.

No separate transcript artifact is generated. The transcript exists in both
the materialized `capture/source.md` and processed `capture/message.md`; the
original voice file remains under `capture/attachments/` and the original
Telegram data remains in `capture/telegram.json`.

## Text cleaning and URL/title enrichment

Text cleaning runs first and reuses `clean_text` with its internal link
resolution disabled. It normalizes social-media formatting, strips invisible
characters and look-alike letters that can hide a link from the resolver, and
removes URL tracking parameters from links already present in the body. Ordered
segment headings are protected while it runs.

## Link discovery

Link discovery builds one ordered table of every distinct target the item
carries, and writes it to `links.json` at the item root. It never uses the
network.

Links are collected from the retained Telegram entities first — both the visible
`url` type and the `text_link` type whose destination the plain message text
drops — and then from the Markdown links and bare URLs in the cleaned body, which
covers voice transcripts and anything the entities missed. Each link is unwrapped
when an outbound redirector carries its destination in the URL itself, then
canonicalized: the scheme and host are lower-cased, a default port is dropped,
and sender-identifying parameters (`utm_*`, `igsh`, `si`, `is`, `fbclid`, `rcm`,
`cp_landing*`) are removed. Paths and fragments are left alone. Rows are then
deduplicated on the canonical URL, so one target is one row however many times
it appears.

Every row records the position `n`, the raw and canonical URLs, the anchor text
when the link came from an entity, the segment it came from, the handler that
would extract it, its extraction priority, and a status. Research providers rank
highest, then the item's only link, then code repositories, then articles and
documents, and finally links that need a nested extraction. Profile roots,
channels, hashtag pages, image CDNs and store links are ranked last: they are
worth a title and never worth extracting.

Handlers and canonical URLs are provisional for shorteners, which cannot be
followed without a request. URL resolution corrects both.

## URL and title resolution

The URL processor resolves the table in priority order, up to the configured
`resolve_budget`, and records the outcome on each row: `resolved` with a title,
`unresolved` with a stable reason, `skipped` when the budget is spent, or
`duplicate` when two links turn out to share one destination. Nothing is dropped.
Budget exhaustion is normal operation and is not reported as a problem.

It also rewrites the body so it stays readable: bare HTTP(S) URLs, Markdown
autolinks, and inline links whose visible label is exactly their destination
become `[page title](final URL)` when a trustworthy title is available. Existing
links such as `[descriptive title](https://example.com)` — including the ones
restored from Telegram entities — are preserved byte-for-byte and are not
fetched. URLs in code, image links, raw HTML attributes, and Markdown reference
definitions are not title-enriched. Body rewriting shares the same budget, so a
long link list cannot spend more than the item was granted.

Titles come from bounded public HTML metadata (`og:title`, Twitter metadata,
Article/WebPage JSON-LD, `<title>`, then `<h1>`). PDFs prefer document/XMP
metadata and otherwise infer a title from the first page's prominent text; file
names and document body text are not used. HTML and PDF downloads are each
capped at 20 MiB by the shipped configuration. Every redirect hop must remain
public HTTP(S); credential-bearing and private-network destinations are rejected.
When an ordinary request encounters a block or unusable response, the resolver
retries anonymously with the same Chrome-compatible HTTP client used by the
Medium extractor. It does not run a browser, execute JavaScript, or authenticate.

If a destination resolves but has no trustworthy title, the final URL remains
as bare text. Request, redirect, content-type, size, safety, and missing-title
problems produce stable partial reasons. Usable output is retained and the item
remains deliverable.

Because enrichment runs after cleaning, the destinations and titles it inserts
are not cleaned. Tracking parameters on a resolved destination therefore survive
in `capture/message.md`, while `links.json` carries the canonical form. Do not
add a second cleaning pass.

The standalone `info-triage-resolve-urls` command performs title enrichment by
default. `--urls-only` restores redirect-only output, `--cache` persists final
URLs and successful titles, and `--report` writes a per-URL JSON audit. The
`--max-html-bytes` and `--max-pdf-bytes` limits match the processor controls;
`--strict` exits nonzero after writing output when any attempted enrichment has
an expected problem.

All configured steps run through shared telemetry. A clean run is `succeeded`;
a completed run with recoverable target-level issues is `partial`; and a
declared `failed` run has its changes discarded before the next step. Unexpected
Python exceptions are logged with their type and traceback and remain serious
item failures. `data/logs/processor-runs.jsonl` contains one compact JSON event
per physical line. Successful events never contain processor inputs or results;
problem events contain the untruncated input Markdown and failed targets, but no
transformed result or binary media.

## Revisions and existing items

Enabling a processor does not scan, move, or rewrite existing ready items. New
captures use the current catalogue. If an older item later receives a Telegram
edit or category change, that new revision goes through the current pipeline.
Each revision is reconstructed from the retained Telegram payload rather than a
previously processed `capture/message.md`, and revision checks prevent a slow result
from overwriting a newer edit.

## Standalone extractors

The Instagram downloader, Instagram OCR/transcription preparation, YouTube
extractor, and LinkedIn extractor remain standalone tools. The YouTube command
does not create captured items or register a processing step. The text cleaner
and URL/title resolver retain their standalone commands while also serving as
configured Telegram processors.

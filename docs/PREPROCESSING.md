# Preprocessing Catalogue

This file is the authoritative catalogue of automatic preprocessing applied to
captured items. `config.yaml` enables processors by listing them under the
ordered `processing.steps` sequence.

## Current behaviour

| Telegram content | Automatic preprocessing | `message.md` result |
| --- | --- | --- |
| Voice note (`voice`) | Transcribe locally, resolve URLs, clean text | Transcript in its ordered `voice` segment |
| Plain text or caption | Resolve recognized short URLs, clean text | Processed text in its ordered segment |
| Location or venue | Resolve URLs, clean text | Existing readable location block in its segment |
| Document | Resolve URLs and clean accompanying caption | Caption in its segment |
| Photo | Resolve URLs and clean accompanying caption | Caption in its segment |
| Video | Resolve URLs and clean accompanying caption | Caption in its segment |
| Animation | Resolve URLs and clean accompanying caption | Caption in its segment |
| Generic audio (`audio`) | Resolve URLs and clean accompanying caption | Caption in its segment |
| Video note (`video_note`) | Resolve URLs and clean accompanying caption | Caption in its segment |

Original downloaded attachments and the complete Telegram payload are always
retained. `source.md` contains the segmented text after source materialization
(including voice transcription) but before URL resolution or cleaning.
`message.md` contains the transformed body with category front matter.

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
the materialized `source.md` and processed `message.md`; the original voice file
remains under `attachments/` and the original Telegram data remains in
`telegram.json`.

## URL resolution and text cleaning

URL resolution reuses the standalone resolver's cautious default: only known
shorteners and deterministic redirect wrappers are fetched unless
`resolve_all` is explicitly enabled. Timeout, retry count, and the maximum HTML
bytes inspected for page-level redirects are documented in `config.yaml`.
Resolution failures warn and retain the utility's original or safely extracted
partial URL. The processor run is recorded as partial with one stable reason per
failed URL, but the usable output is retained and the item remains deliverable.

Text cleaning runs afterward and reuses `clean_text` with its internal link
resolution disabled. This normalizes social-media formatting and removes URL
tracking parameters without making the network request twice.

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
previously processed `message.md`, and revision checks prevent a slow result
from overwriting a newer edit.

## Standalone extractors

The Instagram downloader, Instagram OCR/transcription preparation, and LinkedIn
extractor remain standalone tools. The text cleaner and URL resolver retain
their standalone commands while also serving as configured Telegram processors.

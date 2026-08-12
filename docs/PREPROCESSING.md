# Preprocessing Catalogue

This file is the authoritative catalogue of automatic preprocessing applied to
captured items. A processor runs only when its trigger is present. All other
captured content passes through unchanged.

## Current behaviour

| Telegram content | Automatic preprocessing | `message.md` result |
| --- | --- | --- |
| Voice note (`voice`) | Transcribe locally with Whisper | `Voice note: <recognized text>` at the voice message's position |
| Plain text or caption | None | Original text or caption |
| Location or venue | None | Existing readable location block |
| Document | None | Accompanying caption only |
| Photo | None | Accompanying caption only |
| Video | None | Accompanying caption only |
| Animation | None | Accompanying caption only |
| Generic audio (`audio`) | None | Accompanying caption only |
| Video note (`video_note`) | None | Accompanying caption only |

Original downloaded attachments and the complete Telegram payload are always
retained. The table describes the laptop-facing Markdown; it does not replace
or modify source media.

## Voice-note transcription

A voice item is detected from an attachment-manifest entry whose `kind` is
`voice`. Generic audio uploads, videos, and video notes do not trigger this
processor. Every downloaded voice attachment in a grouped item is transcribed
in attachment order.

The processor reuses the local transcription implementation used for Instagram
video audio. Backend, model, cache directory, and CPU threads use the existing
`INSTAGRAM_TRANSCRIPTION_*` environment settings. The Synology container uses
its image-bundled multilingual `faster-whisper` small model with CPU/int8
inference. Runtime model downloads remain disabled in that container.

Language is detected automatically, speech stays in its original language,
voice-activity detection is enabled to suppress silence and music, and no
timestamps are added. A successful voice-only item has this body after the
category front matter:

```markdown
Voice note: Recognized speech goes here.
```

In a grouped item, each voice line is rendered at the corresponding Telegram
message's logical position. Other text, captions, and locations are preserved.
For a captioned voice message, the voice line comes immediately before its
caption. Existing forwarded-source handling is also preserved: forwarded
content comes first and an adjacent personal note remains under `## Note`.

If Whisper detects no speech, the item is delivered with:

```markdown
Voice note: [No speech recognized]
```

A missing or unavailable attachment, an invalid media file, a file without an
audio stream, or a transcription error fails the processing job. The item stays
in `data/staging/` with SQLite status `failed` and the error is visible on the
dashboard. It is not delivered with a misleading or partial transcript.

No separate transcript artifact is generated in this first version. The
transcript exists in `message.md`; the original voice file remains under
`attachments/` and the original Telegram data remains in `telegram.json`.

## Revisions and existing items

Enabling a processor does not scan, move, or rewrite existing ready items. New
captures use the current catalogue. If an older item later receives a Telegram
edit or category change, that new revision goes through the current pipeline
and its voice attachments are transcribed. Revision checks prevent a slow
transcription result from overwriting a newer edit.

## Standalone extractors

The Instagram downloader, Instagram OCR/transcription preparation, LinkedIn
extractor, text cleaner, and URL resolver remain standalone tools. Except for
reusing the local speech-transcription engine for Telegram voice notes, they are
not automatic item preprocessors.

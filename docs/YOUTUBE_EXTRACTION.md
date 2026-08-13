# Standalone YouTube extraction

`youtube-extract` is an offline preparation tool. It is intentionally not a
Telegram processor: it does not register a `ProcessingWorker`, modify inbox
items, or generate `message.md`.

Install the pinned yt-dlp nightly and its EJS support with the normal project
environment:

```bash
uv sync
```

Extract one or more individual videos:

```bash
uv run youtube-extract https://www.youtube.com/shorts/_-6hzpk9cG8
uv run youtube-extract --input-file tests/fixtures/youtube_urls.txt
```

Only individual `watch`, `shorts`, and `youtu.be` URLs are accepted. Playlists,
channels, and live-in-progress streams are rejected. `/shorts/` URLs are Shorts;
other URLs use a conservative portrait-or-square and at-most-180-second
fallback. Use `--kind short` or `--kind video` when that inference is wrong.

Anonymous extraction is the default. For a public video that requires the same
access as a browser session, explicitly use `--cookies-file PATH` or
`--cookies-from-browser BROWSER`. Advanced yt-dlp options can be repeated:

```bash
uv run youtube-extract \
  --cookies-from-browser safari \
  --extractor-args 'youtube:player_client=web' \
  URL
```

Cookie values and command arguments are never written to extractor status.
There is no proxy rotation or account-bypass automation.

## Behaviour

Every video gets a bounded metadata, comments, and original-language caption
pass. The metadata pass runs before comments, preserving YouTube's original
total comment count. The default comment snapshot is five top-ranked parents,
ten replies total, two replies per thread, and depth two.

One transcript is selected. A matching human-authored original-language track
is preferred, followed by an original automatic-caption track. Auto-translated,
malformed, very short, or severely truncated tracks are rejected. A Short then
falls back to the existing local Whisper implementation with VAD; a normal
video never downloads audio or invokes local ASR.

Shorts always download media and reuse Instagram's tuned frame OCR defaults:
sampled mode, 3 fps, maximum height 800, and batch size 8. A combined stream near
that working resolution is preferred. If YouTube offers no combined stream,
video and audio are downloaded independently, without FFmpeg. Normal videos do
not retain media. OCR, ASR, comments, and captions are non-fatal stages, so
usable metadata survives their failure.

Useful controls include `--skip-comments`, `--skip-download` (reuse existing
Short media), `--skip-ocr`, `--skip-transcript`, `--kind`, the comment/retry
limits, and the same OCR/transcription tuning arguments as Instagram. CLI values
override the strict `extractors.youtube` section in `config.yaml`.

`no_speech` from local Whisper is a valid explicit outcome for a Short whose
audio contains no recognizable speech.

## Output

The default output is `youtube_output/<video_id>/`:

```text
metadata.json          normalized stable fields
metadata_raw.json      complete yt-dlp response shape with access values redacted
description.txt
comments.json          bounded parents with nested replies
comments.txt
transcript.json        selected transcript, language, provenance, and quality
transcript.txt         only the selected transcript text
status.json
llm_input.json
llm_input.txt
media/                 Shorts only
ocr/                   Shorts only
ocr_text.txt            Shorts only when OCR runs
```

## Managed yt-dlp updates

The lockfile pins a reviewed nightly. Extraction runs yt-dlp out of process and
checks the configured official release channel at most once per 24 hours. A
candidate is accepted only after its published SHA-256 matches and `--version`
succeeds; replacement is atomic and retains the prior executable. An offline or
failed check is recorded as a warning and extraction continues with the current
pinned/managed copy. Compatibility-shaped failures force one update check.
Transient failures retry with exponential backoff. If a new executable fails a
request that the last known-good executable completes, the old version is
restored.

The managed executable and update state live under the configured data
directory at `tools/yt-dlp/`. Docker includes Deno for yt-dlp's EJS challenge
support and deliberately does not install FFmpeg.

## Live verification

The ten representative URLs are in `tests/fixtures/youtube_urls.txt`. Normal
tests mock network, model inference, and media. The opt-in network test is:

```bash
YOUTUBE_LIVE=1 uv run pytest tests/extractors/youtube/test_live.py -q
```

It uses temporary output and tool directories. Mutable titles, counts, and
comment text are not asserted.

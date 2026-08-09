# Local Instagram extractor

This project downloads each Instagram post into `instagram_output/<shortcode>/` and prepares its media and text for a later LLM step. Instagram network access is handled by Instaloader; OCR is performed locally. No LLM or hosted OCR API is called.

## Setup

The repository pins Python 3.12 because it has the broadest compatibility with the OCR/PyTorch ecosystem.

```bash
UV_CACHE_DIR=.uv-cache uv sync
```

## Authentication

Public post metadata sometimes works anonymously, but Instagram requires a logged-in session for comments and increasingly rate-limits anonymous post access. Do not put a password in a file or command line. Use one of these forms:

1. Import the active session directly from a local browser (recommended on the computer where the script runs):

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file dataset/instagram_urls.txt \
     --cookies-from-browser safari
   ```

   Supported values include `safari`, `chrome`, `chromium`, `brave`, `firefox`, `edge`, `opera`, and `vivaldi`. The chosen browser must already be logged into `instagram.com`. macOS may ask for Keychain permission.

   To fill in the currently missing comments without rerunning OCR, add `--skip-ocr`. Existing media files are reused rather than downloaded again.

2. Supply a Netscape-format `cookies.txt` exported from the logged-in browser:

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file dataset/instagram_urls.txt \
     --cookies-file /absolute/private/path/instagram-cookies.txt
   ```

   It is a tab-separated Netscape/Mozilla cookie jar. It must contain valid cookies for `.instagram.com`, especially `sessionid`, `csrftoken`, and `ds_user_id`. Treat it like a password and keep it outside Git.

3. Supply an existing Instaloader session file plus its username:

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file dataset/instagram_urls.txt \
     --instagram-user YOUR_USERNAME \
     --session-file /absolute/private/path/session-YOUR_USERNAME
   ```

   This is Instaloader's own session format, normally created at `~/.config/instaloader/session-YOUR_USERNAME` by `instaloader --login YOUR_USERNAME`. The script intentionally does not accept an Instagram password.

## Usage

URLs can come from a file, positional arguments, or both:

```bash
UV_CACHE_DIR=.uv-cache uv run instagram-extract \
  --input-file dataset/instagram_urls.txt \
  --max-comments 50

UV_CACHE_DIR=.uv-cache uv run instagram-extract \
  'https://www.instagram.com/reel/SHORTCODE/' SHORTCODE2
```

The default `best` OCR policy uses the deterministic Surya multilingual model for images/carousel slides and Apple Vision for video frames on macOS. This pairing was selected by testing both on the supplied media: Surya was substantially more accurate on text-heavy multilingual slides, while Vision was faster and less prone to reading random background texture in natural video. Model weights are downloaded once on first use and cached locally. Explicit `surya`, `vision`, and `tesseract` modes are also available.

This is a performance default, not a language restriction. To require the same 90+ language Surya model on video as well, use `--video-ocr-engine surya` (or `--ocr-engine surya` for every media type). It is considerably slower. `--image-ocr-engine` and `--video-ocr-engine` independently override either half of the default.

Video `all` mode is the correctness default: it OCRs every decoded source frame for the full duration and records both counts. `--video-mode adaptive` is the explicit faster tradeoff; it still decodes every frame but OCRs periodic frames plus visually changed frames. Progressive and repeated lines are merged temporally, while the frame-level observations remain in JSON for auditability.

Useful controls:

```text
--max-comments N             write at most N comments, ranked by likes
--comment-scan-limit N       scan this many before ranking; 0 scans all (default)
--video-mode adaptive|all
--video-sample-fps 2.0       periodic OCR rate in adaptive mode
--video-change-threshold 11  lower values OCR more changed frames
--skip-download              rerun OCR over already-downloaded media
--skip-ocr                   download only
--skip-image-ocr             leave existing carousel/image OCR untouched
--skip-video-ocr             leave existing video OCR untouched
--rededuplicate-only         rebuild compact text from stored frame OCR
```

## Output

Each `instagram_output/<shortcode>/` contains:

```text
caption.txt                  original post caption
comments.json / comments.txt
metadata.json                stable, normalized metadata
metadata_raw.json            raw Instagram structures for audit/future fields
status.json                  partial failures are recorded here
media/                       all original images/videos, in carousel order
ocr/*.ocr.json               every OCR observation, bbox, confidence, and time
ocr/*.ocr.raw.txt            all deduplicated text, including low-confidence noise
ocr/*.ocr.txt                confidence-filtered, deduplicated text for one item
ocr/status.json              OCR backend and per-file errors
ocr_text.txt                 combined, LLM-ready visual text for the post
```

Comment output separately identifies the chronologically first scanned comment/reply by the post owner, and includes like counts so downstream code can re-rank the selected comments.

## Accuracy notes

“Any language” means automatic multilingual recognition over the scripts supported by the installed model; no OCR system can guarantee every written language or illegible frame. The JSON confidence scores and raw observations make uncertain results visible. The extractor handles visual/on-screen text only—it does not transcribe speech from the audio track.

# Local Instagram extractor

This project downloads each Instagram post into `instagram_output/<shortcode>/` and prepares its media and text for a later LLM step. Instagram network access is handled by Instaloader; OCR is performed locally. No LLM or hosted OCR API is called.

## Setup

The repository pins Python 3.12 because it has the broadest compatibility with the OCR/PyTorch ecosystem.

```bash
# Core install: the portable ONNX engine, no PyTorch.
UV_CACHE_DIR=.uv-cache uv sync

# macOS only: adds Surya for the most accurate carousel-image OCR (~2-3 GB).
UV_CACHE_DIR=.uv-cache uv sync --extra surya
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

### OCR engines

`--ocr-engine best` resolves per platform:

| Platform | Images / carousel slides | Video frames |
|---|---|---|
| macOS | Surya (if the `surya` extra is installed, else RapidOCR) | Apple Vision |
| Linux (NAS, Pi), everything else | RapidOCR | RapidOCR |

RapidOCR is PP-OCR running on ONNX Runtime. It is the portable engine: no
PyTorch, runs on x86-64 and ARM, and the only practical choice on a NAS or a
Pi, where Surya was measured at 8.3 s/frame. `--image-ocr-engine` and
`--video-ocr-engine` override either half independently.

Recognition covers **English, Spanish and Russian from one model** — the
PP-OCRv5 Cyrillic recognizer, whose charset was verified to cover Latin,
Cyrillic and Spanish accents in full. Chinese needs a separate model; enable it
by uncommenting `"ch"` in `DEFAULT_SCRIPTS` in `instagram_extractor/engines.py`.
With more than one script enabled the engine calibrates on the first frames
containing text and then keeps the best-scoring model for the rest of the file.
`--rec-script` forces a specific one.

### Video sampling

On-screen text stays put for seconds, so OCRing every frame re-reads the same
words dozens of times. The default samples **3 frames per second**, which OCRs
10.6% of frames and retains 89.3% of substantial overlay text; what it drops is
dominated by unstable single-frame noise. `--video-mode all` restores exhaustive
OCR for regression comparisons. See [docs/ocr-video-findings.md](docs/ocr-video-findings.md)
for the full measurements and for the approaches that were tried and rejected.

Useful controls:

```text
--max-comments N             write at most N comments, ranked by likes
--comment-scan-limit N       scan this many before ranking; 0 scans all (default)
--video-mode sample|all      sample (default) OCRs --video-sample-fps frames/second
--video-sample-fps 3.0       measured knee of the accuracy curve
--video-max-height 800       downscale before OCR; 640 is faster, 0 disables
--rec-script auto|cyrillic|latin|ch|...
--threads N                  inference threads (see Resource limits)
--skip-download              rerun OCR over already-downloaded media
--skip-ocr                   download only
--skip-image-ocr             leave existing carousel/image OCR untouched
--skip-video-ocr             leave existing video OCR untouched
--rededuplicate-only         rebuild compact text from stored frame OCR
```

## Benchmarking a machine

Run the same command on every target to get a directly comparable figure:

```bash
UV_CACHE_DIR=.uv-cache uv run instagram-ocr-bench --label "my machine"
```

It reports wall clock, peak memory, and **how many seconds one minute of video
costs**, appending each run to `bench_results.json`. Measured so far, over
437.6 s of video:

| Machine / engine | Per minute of video | Peak RSS |
|---|---|---|
| macOS, Apple Vision, sampled | 16 s | 412 MB |
| macOS, RapidOCR, 2 threads | 47 s | 566 MB |

## Docker (Synology NAS, Raspberry Pi)

The image excludes PyTorch and bakes the ONNX models in, so the container never
downloads anything at runtime.

```bash
# Build for the NAS from an ARM Mac
docker buildx build --platform linux/amd64 -t instagram-extractor:latest --load .

docker compose run --rm instagram-extract --input-file /data/dataset/instagram_urls.txt
```

Resulting image: ~461 MB.

### Resource limits

A **Dockerfile cannot set CPU or memory limits** — image build and runtime
resource control are separate concerns. They live in `docker-compose.yml`
(`cpus`, `mem_limit`), on `docker run --cpus/--memory`, or in the Synology
Container Manager UI.

Limits alone are not enough. ONNX Runtime and OpenMP size their thread pools from
the **host** CPU count and ignore the cgroup quota, so a container capped at 2
CPUs would otherwise start a thread per host core and lose the time to context
switching. The `INSTAGRAM_OCR_THREADS` environment variable in the Dockerfile
pins this, and when it is unset the code reads the cgroup quota directly. Keep it
equal to the `cpus` limit.

Defaults are tuned for the NAS (2-core R1600): 2 CPUs, 2 GB memory, 2 threads.
Measured peak usage is 566 MB, so the memory limit is a blast-radius guard rather
than a fit — exceeding it means an OOM kill, so do not trim it close.

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

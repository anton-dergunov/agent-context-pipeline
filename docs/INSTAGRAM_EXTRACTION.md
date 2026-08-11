# Instagram Extraction

This project downloads each Instagram post into `instagram_output/<shortcode>/` and prepares its media, on-screen text, and spoken audio for a later LLM step. Instagram network access is handled by Instaloader; OCR and transcription are performed locally. No LLM, hosted OCR, or hosted transcription API is called.

## Setup

The Linux image pins Python 3.12 because it has the broadest compatibility with
the OCR ecosystem. Local installs support Python 3.12 through 3.14; use 3.12 or
3.13 when Apple Vision OCR is required because current PyObjC Vision wheels do
not support Python 3.14.

```bash
# Core install: the portable ONNX engine, no PyTorch.
UV_CACHE_DIR=.uv-cache uv sync

# Apple Silicon: adds MLX Whisper for medium-model transcription on the Metal GPU.
UV_CACHE_DIR=.uv-cache uv sync --extra mac-transcription

# Optional macOS image OCR (~2-3 GB in addition to the core install).
UV_CACHE_DIR=.uv-cache uv sync --extra surya --extra mac-transcription
```

## Authentication

Public post metadata sometimes works anonymously, but Instagram requires a logged-in session for comments and increasingly rate-limits anonymous post access. Do not put a password in a file or command line. Use one of these forms:

1. Import the active session directly from a local browser (recommended on the computer where the script runs):

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file benchmarks/data/instagram_urls.txt \
     --cookies-from-browser safari
   ```

   Supported values include `safari`, `chrome`, `chromium`, `brave`, `firefox`, `edge`, `opera`, and `vivaldi`. The chosen browser must already be logged into `instagram.com`. macOS may ask for Keychain permission.

   To fill in the currently missing comments without rerunning OCR, add `--skip-ocr`. Existing media files are reused rather than downloaded again.

2. Supply a Netscape-format `cookies.txt` exported from the logged-in browser:

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file benchmarks/data/instagram_urls.txt \
     --cookies-file /absolute/private/path/instagram-cookies.txt
   ```

   It is a tab-separated Netscape/Mozilla cookie jar. It must contain valid cookies for `.instagram.com`, especially `sessionid`, `csrftoken`, and `ds_user_id`. Treat it like a password and keep it outside Git.

3. Supply an existing Instaloader session file plus its username:

   ```bash
   UV_CACHE_DIR=.uv-cache uv run instagram-extract \
     --input-file benchmarks/data/instagram_urls.txt \
     --instagram-user YOUR_USERNAME \
     --session-file /absolute/private/path/session-YOUR_USERNAME
   ```

   This is Instaloader's own session format, normally created at `~/.config/instaloader/session-YOUR_USERNAME` by `instaloader --login YOUR_USERNAME`. The script intentionally does not accept an Instagram password.

## Usage

URLs can come from a file, positional arguments, or both:

```bash
UV_CACHE_DIR=.uv-cache uv run instagram-extract \
  --input-file benchmarks/data/instagram_urls.txt \
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
by uncommenting `"ch"` in `DEFAULT_SCRIPTS` in `info_triage/extractors/instagram/engines.py`.
With more than one script enabled the engine calibrates on the first frames
containing text and then keeps the best-scoring model for the rest of the file.
`--rec-script` forces a specific one.

### Video sampling

On-screen text stays put for seconds, so OCRing every frame re-reads the same
words dozens of times. The default samples **3 frames per second**, which OCRs
10.6% of frames and retains 89.3% of substantial overlay text; what it drops is
dominated by unstable single-frame noise. `--video-mode all` restores exhaustive
OCR for regression comparisons. See [research/ocr-video-findings.md](research/ocr-video-findings.md)
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
--transcription-backend best|faster-whisper|mlx
--transcription-model tiny|base|small|medium|large-v3|turbo
--transcription-language CODE  force a spoken language; default detects it
--transcription-threads N    CPU helper threads; default 1
--[no-]transcription-vad     suppress silence/music hallucinations; default on
--transcription-model-cache-dir PATH
--skip-download              rerun OCR over already-downloaded media
--skip-ocr                   skip visual OCR; transcription still runs
--skip-transcription         leave existing transcript artifacts untouched
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

### Speech transcription

The reviewed production defaults are platform-aware:

| Platform | Backend | Model | Compute | Threads |
|---|---|---|---|---:|
| Apple Silicon Mac | MLX Whisper | medium | Metal GPU, FP16 | 1 CPU helper |
| Synology NAS (`linux/amd64`) | faster-whisper | small | CPU, int8 | 1 |
| Raspberry Pi 4, 64-bit OS (`linux/arm64`) | faster-whisper | small | CPU, int8 | 1 |

VAD is enabled conservatively by default. Instrumental/no-speech media produces
a successful empty transcript instead of invented words. Spoken language is
preserved; the extractor never translates. Both backends record the detected
language and its probability (unless the user explicitly fixes the language).

All choices can be overridden through the CLI controls above or with
`INSTAGRAM_TRANSCRIPTION_BACKEND`, `INSTAGRAM_TRANSCRIPTION_MODEL`, and
`INSTAGRAM_TRANSCRIPTION_THREADS`. For example:

```bash
# Explicitly use the normal Mac default.
UV_CACHE_DIR=.uv-cache uv run --extra mac-transcription instagram-extract \
  --input-file benchmarks/data/instagram_urls.txt \
  --transcription-backend mlx --transcription-model medium
```

The evaluation suite and its metrics dependencies are installed by default:

```bash
UV_CACHE_DIR=.uv-cache uv run instagram-transcription-bench \
  --telegram-audio /absolute/path/to/first.ogg \
  --telegram-audio /absolute/path/to/second.ogg
```

It tests every downloaded video named by `benchmarks/data/instagram_urls.txt`, the two
explicit audio files, and a fixed ten-sample FLEURS set for each of English,
Spanish, Russian, and Mandarin. Audio and model downloads stay in ignored cache
directories. The report compares ROUGE-L and token Jaccard against both FLEURS
reference text and large-v3, and includes peak memory, timing, exact real-world
transcripts, and VAD-on/off results for the expected music-only posts.

MLX must run in a macOS process that can access Metal. Sandboxed/headless tools
that hide the GPU can abort inside MLX while importing its native extension;
run the extractor from a normal Terminal/session in that environment. This does
not affect Docker, which uses faster-whisper on CPU and does not install MLX.

## Docker (Synology NAS, Raspberry Pi)

The image excludes PyTorch/MLX and bakes both RapidOCR and multilingual Whisper
`small` into the image. `HF_HUB_OFFLINE=1` prevents runtime downloads, and the
build validates that the checkpoint loads on the target architecture.

```bash
# Build the unified app image for the NAS from an ARM Mac.
docker buildx build --platform linux/amd64 -t info-triage:latest --load .

# Run the extractor CLI from that same image with explicit input/output mounts.
docker run --rm --entrypoint instagram-extract \
  -v "$PWD/benchmarks:/work/benchmarks:ro" \
  -v "$PWD/instagram_output:/work/instagram_output" \
  info-triage:latest \
  --input-file /work/benchmarks/data/instagram_urls.txt \
  --output-dir /work/instagram_output
```

The same Dockerfile builds `linux/amd64` for Synology and `linux/arm64` for the
Pi. The Pi requires 64-bit Raspberry Pi OS. Locally verified image sizes were
approximately 1.28 GB (`amd64`) and 1.15 GB (`arm64`), including the 486 MB
Whisper checkpoint.

### Resource limits

A **Dockerfile cannot set CPU or memory limits** — image build and runtime
resource control are separate concerns. They live in `compose.yaml`, in
`docker run` options, or in the Synology Container Manager UI.

The Synology kernel does not expose the CPU CFS scheduler support required by
Docker's `NanoCPUs` quota, so the Compose configuration does not set `cpus`.
Instead, `cpu_shares: 512` gives the container a lower relative CPU priority
during contention; it is not a hard one-CPU limit. ONNX Runtime, CTranslate2,
and OpenMP can size thread pools from the **host** CPU count, so the image also
pins both OCR and transcription to one thread.

Defaults are tuned for the upgraded NAS (2-core/4-thread Ryzen R1600, 20 GB
memory): lower relative CPU priority, an 8 GB hard memory ceiling, and one
compute thread. The limit is a ceiling rather than a reservation and leaves
roughly 12 GB for DSM, filesystem cache, and other containers. Setting
`memswap_limit` to the same value prevents additional container swap usage.
Whisper small measured 1,347 MB peak RSS on the benchmark Mac; the extra
headroom supports larger future workflows. OCR and transcription models are
loaded sequentially so their peaks do not add together.

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
transcripts/<media>.txt      plain spoken text, no timestamps
transcripts/<media>.json     status, model, language, probability, and text
transcripts/status.json      per-media transcription outcome/errors
transcript.txt               combined spoken text for the post
llm_input.json / .txt        metadata, comments, visual text, and spoken audio
```

Comment output separately identifies the chronologically first scanned comment/reply by the post owner, and includes like counts so downstream code can re-rank the selected comments.

## Accuracy notes

“Any language” means automatic multilingual recognition over the languages supported by the installed models; no OCR or speech model can guarantee every language or unclear input. The JSON confidence/probability fields and explicit empty/failure statuses make uncertainty visible.

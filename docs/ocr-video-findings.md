# Video OCR: measurements and design decisions

Everything here was measured on the working corpus in `instagram_output/`:
**9 videos, 437.6 s, 12,493 decoded frames**, plus **43 carousel images** with
476 stored text segments. Machine: Apple Silicon, macOS.

The point of this document is that the rejected options are recorded with the
number that killed them, so they do not get re-proposed.

---

## 1. Where the time went

| Stage | Measured |
|---|---|
| Apple Vision, every frame, full resolution | 136 ms/frame → **~28 min** |
| Decode only (`cv2.read()` every frame) | 30.0 s |
| Post-processing (`merge_frames`, all 9 videos) | **22 s** (worst single video 16.1 s) |

Per-frame OCR was ~98% of the cost. The deduplication/post-processing layer was
never the problem, so it was left alone.

## 2. What was tried and rejected

### Frame-difference gating (the existing `adaptive` mode) — removed

`adaptive` diffed a 32×32 grayscale thumbnail of the whole frame. That measures
**camera and scene motion**, not text change. Overlay text is composited on top
of moving video, so the thing worth detecting is precisely the part that does
*not* move.

- Full-frame difference at the shipped threshold of 11 selected 1,243 / 12,493
  frames (10%), but the selection tracked motion: a 59 s handheld clip triggered
  386 times while a locked-off clip triggered 15.
- A much stronger detector — high-pass glyph mask, designed to suppress soft
  background motion and keep sharp glyph strokes — still selected **80–100% of
  frames** at every threshold tried (0.008 / 0.015 / 0.03).

Pixel differencing cannot separate "the text changed" from "the background
moved". The mode and `--video-change-threshold` were deleted rather than left as
a knob that looks useful and is not.

### Per-region pixel caching — rejected

The idea: hash the pixels inside each detected text box and reuse the previous
recognition when the hash repeats. Measured over 9,783 detected boxes:

- **8,524 unique hashes = 87.1% unique.** Almost nothing would have been cached.
- Whole-frame hashes: 7,645 unique out of 7,714 frames.

Causes: detector boxes jitter by a pixel or two between frames, and the video
moves *behind* semi-transparent glyphs. Exact-match caching is not viable here.

### Vision's `Fast` recognition level — rejected

| Setting | ms/frame | distinct strings |
|---|---|---|
| Accurate, full res | 136 | 7 |
| Accurate, half res | 72 | 7 |
| Fast, full res | 26.7 | **36** |
| Fast, half res | 15.7 | **35** |

`Fast` is 5× quicker and produces unstable garbage — 36 spellings where
`Accurate` found 7 stable ones. Speed here comes at the cost of the entire
output.

### Tesseract — rejected as a portable fallback

On the same frames Apple Vision read cleanly:

| Engine | Time | Output |
|---|---|---|
| Vision | 120 ms | `Wait until you see this village after dark...` |
| Tesseract | 799 ms | `['Se','№','3','es','til','you','see','village','Е','%','Г.','\']` |

Slower *and* unusable. Not a fallback.

### Surya for video — rejected

**8.3 s/frame** on Apple Silicon (11.1 s model load) — 60× slower than Vision.
On a 2-core R1600 with no GPU it would be far worse. Surya remains the best
engine for text-heavy carousel *images* and is kept for that on macOS, as an
optional extra.

### PP-OCRv6 — rejected

v6 ships a single `multi_PP-OCRv6_rec_*` model covering 52 languages, which
would have removed the need for per-script models entirely. Its charset was
inspected directly:

| Model | Size | Charset | Latin | Cyrillic | Spanish |
|---|---|---|---|---|---|
| `PP-OCRv6_rec_small` | 21.2 MB | 18,708 | 100% | **0%** | 93.3% |

No Cyrillic. Russian is a requirement, so v6 is out.

### Detector-gated recognition — viable, not needed

Vision's detector-only request costs **3.6–15.1 ms** against **47–108 ms** for
full recognition, a 10–20× ratio, so gating recognition on a cheap detector pass
is a real option. It was not adopted because plain temporal sampling already
removes ~90% of the work with a simpler mechanism. It remains the fallback if
short-lived (<350 ms) text ever needs catching.

## 3. What worked: sample in time, not in pixels

Overlay text persists for seconds, so most frames re-read words already read.
Scored against the all-frames output, counting only *substantial* text (≥8
alphanumerics, on screen ≥0.8 s):

| Sample rate | Frames OCR'd | % of original | Substantial-text recall |
|---|---|---|---|
| 6 fps | 2,634 | 21.1% | 92.4% |
| 4 fps | 1,675 | 13.4% | 88.4% |
| **3 fps** | **1,319** | **10.6%** | **89.3%** |
| 2 fps | 882 | 7.1% | 87.9% |
| 1 fps | 443 | 3.5% | 71.4% ← cliff |

Flat from 6 → 2 fps, collapsing at 1. **3 fps is the knee** and is the default.

Critically, the drops are noise. Inspecting what disappears at 3 fps:

- **Lost:** `'BO'`, `'wwww'`, `'23E3250'`, `'© HIKE:'`, `'Remaiting Likin Ambann'`,
  `'sti may fati'`, and stray single characters read off shop signs.
- **Kept:** `'ВЕЧЕРНЯЯ СКАЗКА КИТАЯ'`, `'Почему туристы в Шанхае'`,
  `'Wait until you see this village after dark.'`, `'Sea World $0'`, and the rest
  of the real overlays.

Sampling preferentially discards noise because noise is temporally unstable and
real overlays are not. The headline recall understates the practical quality.

### Decoding

`cap.grab()` advances without converting a frame to BGR; only sampled frames pay
for `retrieve()`. Over the corpus: **30.0 s → 13.8 s (2.2×)**.

### Frame height

Chosen from the accuracy/time curve with the ONNX engine at 3 fps:

| Max height | ms/frame | Recall |
|---|---|---|
| 640 | 140 | 61.9% |
| **800** | **198** | **71.4%** |
| 960 | 279 | 71.4% |
| 1280 (native) | 410 | 71.4% |

800 buys full accuracy at half the cost of native. 640 saves a further 1.4× but
loses ~10 points, so it is offered as a speed dial rather than the default.

(For Apple Vision the picture differs — half resolution was byte-identical to
full — but a single default of 800 is safe for both engines.)

## 4. Filter thresholds had to become time-based

`useful()` rejected segments seen fewer than 3 times, with the short-text
threshold scaled by `source_fps`. But `observations` counts frames **actually
OCR'd**, so any sampling silently tightened the filter — the exact bug that made
the old `adaptive` mode look worse than it was. At 3 fps a flat "3 observations"
demands a full second on screen.

Thresholds are now derived from `effective_fps = frames_ocrd / duration`:

```
minimum    = max(2, ceil(effective_fps * 0.10))
short text = max(3, ceil(effective_fps * 0.25))
```

Rounding **up** reads as "visible for at least this long" and reproduces the old
behaviour exactly on real footage — (3, 8) at 30 fps, (3, 7) at 25, (3, 6) at 24.
A plain `round()` would have loosened the 24/25 fps cases to 2 and admitted
single-frame noise; this was caught because one video's output changed.

**Verification:** rebuilding all 52 stored results through the new code produced
**byte-identical output**. Measured benefit at 3 fps: recall across all strings
rose 53.5% → 62.7%.

## 5. Engine selection

`--ocr-engine best` previously mapped video to Apple Vision with no platform
check, and `make_engine` re-raises for an explicitly named engine — so the
default configuration **could not run on Linux at all**. Resolution is now
per platform: macOS uses Surya for images and Vision for video; everything else
uses the ONNX engine for both.

### One model covers English, Spanish and Russian

PP-OCR recognition models are grouped by script and detection is shared. Charsets
were inspected directly rather than trusted from documentation:

| Model | Size | Charset | Latin | Cyrillic | Spanish |
|---|---|---|---|---|---|
| `cyrillic_PP-OCRv5_rec_mobile` | 8.1 MB | 851 | 100% | 100% | 100% |
| `eslav_PP-OCRv5_rec_mobile` | 7.9 MB | 518 | 100% | 100% | 100% |
| `latin_PP-OCRv5_rec_mobile` | 7.9 MB | 503 | 100% | 3% | 100% |

The **Cyrillic** model covers all three required languages, so the default needs
no per-frame language guessing. Confirmed on video:

| Recognition model | ms/frame | Recall |
|---|---|---|
| cyrillic | 198 | 71.4% |
| eslav | ~200 | 71.4% |
| latin | 279 | 47.6% |

Chinese needs a separate model (`ch`), so the multi-script machinery is kept:
adding an entry to `DEFAULT_SCRIPTS` in `engines.py` enables calibration, which
scores each candidate over the first frames containing text and then locks in
the winner. With one entry — the default — calibration never runs.

### Image quality: the cost of dropping Surya on Linux

RapidOCR against the stored Surya output over 43 carousel images / 476 segments:

| Metric | Result |
|---|---|
| Exact line match | 347/476 = 72.9% |
| Fuzzy line match (≥88) | 390/476 = 81.9% |
| **Character coverage** | **83.3%** |
| Lines Surya did not report | 311 |
| Speed | ~1.1–1.5 s/image |

The line-level metrics understate it: the engines segment differently, with
Surya returning a whole paragraph as one line where PP-OCR returns several.
Raising the detector budget (736 → 1600) changed nothing, so 83.3% is the
engine's ceiling here, not a resolution artifact.

**Decision:** acceptable on Linux, where the alternative is a 3 GB image and
8.3 s/frame. macOS keeps Surya. If carousel accuracy ever matters more than
image size, `--image-ocr-engine surya` still works after installing the extra.

## 6. End-to-end result

Full corpus (437.6 s of video), sampled at 3 fps, max height 800:

| Configuration | Wall clock | Per minute of video | Peak RSS |
|---|---|---|---|
| Apple Vision, every frame (original) | ~28 min | ~230 s | — |
| **Apple Vision, sampled (macOS default)** | 119–139 s | **16–19 s** | 412 MB |
| **RapidOCR cyrillic, 2 threads (Linux default)** | 346–400 s | **47–55 s** | 566 MB |

Ranges are two separate runs of the identical configuration, not a tuning sweep.

**14× faster on macOS.** The ONNX engine on the same machine is ~3× slower than
Vision, which is the price of portability.

Reproduce on any machine with `instagram-ocr-bench`.

### Caveat: measure on a cool machine

These were taken on a laptop, and sustained benchmarking throttles it. Repeating
an identical configuration back to back gave 27 s then 21 s per video-minute — a
~30% spread from thermal state alone. One run mid-session reported 77 s/video-minute
for a configuration that measures 16 s when rested, which initially looked like a
thread-count effect; an interleaved A/B (2 vs 8 threads, two rounds) showed the
two settings within noise of each other and no such penalty. Treat single runs as
indicative, interleave configurations when comparing, and prefer the NAS or Pi
figures, where there is no turbo and no thermal envelope to fall out of.

## 7. Container sizing

| | |
|---|---|
| Image size | **461 MB** (1.51 GB before multi-stage) |
| Baked-in models | 13.5 MB |
| Measured peak RSS | 566 MB |
| `mem_limit` | 2 GB (~3.5× headroom) |

Savings came from dropping ffmpeg (389 MB — OpenCV bundles its own decoders),
avoiding a `chown -R` that duplicated `/app` (321 MB), and keeping PyTorch out.

The memory limit is a blast-radius guard, not a fit: exceeding it means the
kernel OOM-kills the process (exit 137), so it should not be trimmed close to the
measurement. The NAS has 20 GB, so this is not a hardware constraint.

**Thread pinning matters more than the CPU cap.** ONNX Runtime and OpenMP size
their pools from the *host* CPU count and ignore the cgroup quota, so a capped
container would otherwise start a thread per host core and lose the time to
context switching. Verified: with `--cpus=1.5` on a 4-CPU host and no environment
override, the resolver reads the cgroup and returns **1** thread rather than 4.

A Dockerfile cannot set CPU or memory limits — those are runtime concerns
(`docker run --cpus/--memory`, compose, or the Synology Container Manager UI).

## 8. Open item

Throughput on the NAS (R1600, 2 cores) and the Pi 4 is **not yet measured**.
Scaling the 47 s/video-minute figure by a plausible 3–4× for the R1600 suggests
roughly 2.5–3 minutes of wall clock per minute of video. Run
`instagram-ocr-bench` on the NAS to replace this estimate. If it lands worse than
hoped, the dials in order of preference are `--video-max-height 640`
(1.4× faster, ~10 points of recall) and `--video-sample-fps 2` (measured 87.9%).

# Experiment · where are the words of a saved Instagram post?

**Question.** An agent given an Instagram link sees, at best, the caption. How much of what a saved
post says is in the caption, and how much only in its images and video — text drawn on the screen,
or spoken?

**Status.** Counted 3 Oct 2026 over the 52 Instagram posts in the owner's delivered `info` inbox,
captured between 19 Aug and 29 Sep 2026: **in 30 of 52 posts the images and video held more words
than the caption, and in 22 at least three times as many.** Across all 52, captions came to 4,794
words, on-screen text to 13,045 and speech to 1,408.

**Serves.** [`docs/extractors/instagram.md`](../../docs/extractors/instagram.md), and the reason the
index quotes recovered streams before the caption
([`docs/architecture/preprocessing.md`](../../docs/architecture/preprocessing.md)).

## Method

- **Corpus.** Every `extracted/*-instagram-*` directory of a delivered inbox: what the owner actually
  saved, not a test set. 50 of the 52 kept their media: 33 videos and 17 image posts, 14 of them
  carousels.
- **Apparatus.** [`streams.py`](streams.py) counts whitespace-separated words in each extraction's
  `raw/caption.txt`, `raw/ocr_text.txt` and `raw/transcript.txt`, and prints aggregates only:

  ```bash
  uv run python experiments/instagram-streams/streams.py ~/info-triage-inbox/info
  ```

- [`result.json`](result.json) is that output. The posts themselves are private and not committed.

## Results

| | Posts |
|---|---|
| on-screen text and speech together exceed the caption | **30 of 52** |
| … by at least three times | 22 |
| caption under 25 words | 13 |

| Stream | Words |
|---|---|
| caption | 4,794 |
| on-screen text | 13,045 |
| speech | 1,408 |

Two posts make the pattern concrete:

- A reel whose caption is 21 words — "If you wanna get hired as an $100-$200k+ AI Engineer, do these.
  Comment 'hired' to get the full list of projects" — while the list is on the screen. The on-screen
  text recovers all twelve project names and their descriptions, 806 words.
- A twelve-image carousel captioned "10 places off the standard routes of Guangzhou", in Russian, 16
  words. Every place name — Dongshankou, Kuiyuan 1922, Xiaozhou village, Qixinggang ancient coast
  site, Shenjing and Pantang ancient villages — is only in the pictures.

The asking-for-a-comment pattern is the reason this matters to an agent: the caption is often
written to withhold the content, so that the viewer has to engage to get it.

## Limits

- Word counts of OCR output include its noise: misread letters, fragments of partly hidden text,
  and on-screen chrome. The on-screen total overstates the useful text; the per-post comparison is
  robust to that because the gaps are large.
- Speech is small here because many reels are set to music, which voice-activity detection leaves
  empty by design ([`transcription-models/`](../transcription-models/README.md)).
- One inbox, one person's saving habits, one count.

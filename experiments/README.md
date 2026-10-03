# Experiments

Spikes, benchmarks and measured iterations, one directory each. Every directory holds its write-up —
the question, the method and the numbers — and its apparatus where there is one. The document it
serves keeps only the decision, with a link back here.

**Results are committed** when they hold no personal data, credentials or private paths and come to
a few MB of text. Media, model caches and anything that can be regenerated stay ignored, and so does
anything derived from private notes or messages.

Nothing here is imported by the application, and none of it is in the Docker image.

| Experiment | Question | Status | Serves |
| --- | --- | --- | --- |
| [`item-contract/`](item-contract/README.md) | Where does an agent filing saved links spend its budget, and how large may a per-item index be? | Measured 14 Aug 2026: search outnumbers fetch 1.7 to 1, one fetch in ten fails, comments are 38% of an Instagram post and none of its signal. An index costs about 6k tokens for a 20-item day against 30–60k. Write-up only | [`docs/architecture/item-contract.md`](../docs/architecture/item-contract.md) |
| [`related-notes/`](related-notes/README.md) | Can the plans and notes an item belongs with be found without an agent, and does handing them over make routing cheaper? | Measured and built 18 Aug 2026: BM25 then a cross-encoder, no vector index; 88% of emitted pointers correct; hedged wording and a destination hint cut tokens 30%, 40% with the stronger model. One run per condition | [`docs/architecture/related-notes.md`](../docs/architecture/related-notes.md) |
| [`video-ocr/`](video-ocr/README.md) | What can be skipped when reading on-screen text from video, and which engine runs on a NAS? | Measured early Aug 2026: 3 frames per second keeps 89% of substantial text at 11% of the frames, 14 times faster on macOS. NAS throughput not yet measured | [`docs/extractors/instagram.md`](../docs/extractors/instagram.md) |
| [`transcription-models/`](transcription-models/README.md) | Which Whisper model transcribes English, Spanish, Russian and Mandarin well enough on each machine? | Measured 9–10 Aug 2026: `small` on the NAS, `medium` on Apple Silicon; voice-activity detection stays on because every model invents words over music without it | [`docs/architecture/preprocessing.md`](../docs/architecture/preprocessing.md) |
| [`url-extraction-corpus/`](url-extraction-corpus/README.md) | How much of a real link collection can the server retrieve and convert? | Run 13 Aug 2026: 86 of 97 complete, each of the 11 failures with a stable reason | [`docs/extractors/documents.md`](../docs/extractors/documents.md) |
| [`url-title-audit/`](url-title-audit/README.md) | How many bare links can be given a trustworthy title from public metadata alone? | Run twice 13 Aug 2026 over 529 links: 434 titled, then 489 after a larger HTML cap, a browser-compatible retry and first-page PDF titles; 192 of the 193 `lnkd.in` links reached their destination | [`docs/architecture/preprocessing.md`](../docs/architecture/preprocessing.md) |
| [`instagram-streams/`](instagram-streams/README.md) | How much of a saved Instagram post is in its caption, and how much only in its images and video? | Counted 3 Oct 2026 over 52 real posts: in 30 the on-screen text and speech held more words than the caption, in 22 three times as many | [`docs/extractors/instagram.md`](../docs/extractors/instagram.md) |
| [`text-cleaning/`](text-cleaning/README.md) | What does social-media formatting cost an agent in tokens, and what does cleaning recover? | Counted 3 Oct 2026 over a 4,017-line capture backlog: styled-letter lines cost 4.9× their normalized tokens; cleaning saves 8.5% of the file | [`docs/architecture/preprocessing.md`](../docs/architecture/preprocessing.md) |
| [`medium-access/`](medium-access/README.md) | Which routes to a Medium article work, and how much of a member-only story is public? | Run 12 Aug 2026 over five articles: two whole, three previews of about 220–260 words; RSS first, then a browser-compatible request. Write-up only | [`docs/extractors/medium.md`](../docs/extractors/medium.md) |

The URL lists the experiments run over are test fixtures, in
[`tests/fixtures/`](../tests/fixtures/).

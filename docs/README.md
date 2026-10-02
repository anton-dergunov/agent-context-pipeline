# Documentation

The project in one paragraph: things saved during the day are captured with one tap, enriched on an
always-on server, and delivered to a laptop as an inbox in which each item's `index.md` tells a
coding agent everything it needs to file it. The front page is the repository
[`README.md`](../README.md).

## The invariants

- **Nothing captured is ever withheld because enrichment went wrong.** A step that fails is recorded
  on the item, and the item still arrives.
- **One item is one self-contained directory, and its `index.md` is the only file that has to be
  read.** Everything else is addressable, with its cost printed.
- **A field is omitted, never guessed.** An absent field is information; an invented one would be
  trusted.
- **No language model runs in the pipeline.** Extractors retrieve and convert. A truncated quote is
  visibly a fragment; a summary would look complete.
- **Removing an item's directory on the laptop is the whole acknowledgement protocol.**
- **Retrieval, OCR and transcription change only with a measured reason.** The measurements are in
  [`experiments/`](../experiments/README.md).

## The documents

**Architecture** — how the system is built.

- [`architecture/overview.md`](architecture/overview.md) — components, routes, capture, identity, lifecycle, telemetry, SQLite, the HTTP endpoints.
- [`architecture/item-contract.md`](architecture/item-contract.md) — the item directory, `index.md` and its fields, and why they are shaped that way.
- [`architecture/preprocessing.md`](architecture/preprocessing.md) — the catalogue of automatic steps, per route, in order.
- [`architecture/sync.md`](architecture/sync.md) — the laptop inbox, the two generated views, deletion as the processed signal.
- [`architecture/related-notes.md`](architecture/related-notes.md) — pointing each item at the plans and notes it belongs with.

**Extractors** — retrieving one link and converting it to Markdown.

- [`extractors/routing.md`](extractors/routing.md) — which extractor handles a URL, the safety guards, the shared output shape.
- [`extractors/documents.md`](extractors/documents.md) — web pages, PDFs and research papers.
- [`extractors/instagram.md`](extractors/instagram.md) — posts and reels, with on-screen text and spoken audio.
- [`extractors/linkedin.md`](extractors/linkedin.md) — public posts and the author's own comments.
- [`extractors/medium.md`](extractors/medium.md) — articles, member previews, authenticated access.
- [`extractors/youtube.md`](extractors/youtube.md) — metadata, captions, comments, and Shorts.

**Operations** — running it.
[`operations/setup.md`](operations/setup.md) ·
[`operations/synology-deployment.md`](operations/synology-deployment.md) ·
[`operations/tailscale-https.md`](operations/tailscale-https.md)

**Review** — working the inbox.
[`reviewing-in-emacs.md`](reviewing-in-emacs.md)

**Experiments** — the measurements behind the decisions, with their apparatus and results:
[`../experiments/`](../experiments/README.md).

**Plans** — what is not built yet: [`plans.md`](plans.md).

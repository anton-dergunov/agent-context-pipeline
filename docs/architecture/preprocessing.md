# Preprocessing catalogue

The authoritative catalogue of the automatic preprocessing applied to captured items.
`config.yaml` enables steps per route by listing them under that route's ordered `steps`; they run
in exactly that order, and removing an entry disables the step for that route.

All of it is enrichment. No preprocessing failure of any kind withholds an item; see the delivery
guarantee in [`overview.md`](overview.md#the-delivery-guarantee).

## Preprocessing by route

Preprocessing is chosen at capture time by which bot the item was shared to.

| Route | Steps | Why |
| --- | --- | --- |
| `info` | `voice-transcription`, `text-cleaning`, `link-discovery`, `url-resolution`, `content-extraction`, `index-render` | The default route: the item is read later and every enrichment pays for itself there |
| `job` | `link-discovery`, `index-render` | A downstream script already fetches and processes the posting. Discovery is offline and only records the URL so the index can name it |
| `clip` | `link-discovery`, `index-render` | Downloading happens on the laptop. Nothing here transcribes or OCRs a video |
| `lang` | `index-render` | A word and its context. There is no link to discover and the wording is the payload |

On every route but `info` the captured text reaches the laptop byte for byte as it was sent: neither
`link-discovery` nor `index-render` rewrites the body. The rest of this document describes `info`.

## What happens to each kind of content

| Captured content | Automatic preprocessing | Where the text ends up |
| --- | --- | --- |
| Voice note | Transcribed locally, then treated as text | The transcript, in its `voice` segment |
| Text, or a caption on any media | Cleaned; links discovered, resolved and extracted | The processed text, in its segment |
| Location or venue | As text | A readable location block with coordinates and a maps link |
| Document, photo, video, animation, audio, video note | Only the accompanying caption is processed. The file is kept as supplied and is not OCRed or transcribed | The caption, in its segment |

Original attachments and the complete received payload are always retained. Every item ends with
`index.md` at its root. The files an item holds are described in
[`item-contract.md`](item-contract.md).

### Order

The order is validated at startup, per route: transform steps after `voice-transcription`,
`link-discovery` before `url-resolution`, `index-render` last, and no step twice in one route.

- Voice transcription first materializes the complete body, so later steps see one text.
- **Cleaning precedes the link work**, because zero-width characters and look-alike letters can
  attach themselves to a URL and hide it from discovery. Destinations and titles inserted later by
  resolution are therefore not cleaned. Canonicalizing them is link discovery's job, and a second
  cleaning pass is not the answer.
- Discovery is offline and runs before URL resolution, the only step that needs the network for
  links. Content extraction then runs on the resolved table.
- Index rendering runs last, so it sees the finished body, the resolved table and everything that
  was retrieved.

### Segments

Every source message is represented uniformly in `capture/source.md` and `capture/message.md`:

```markdown
## Segment 1 — text

Personal commentary

## Segment 2 — text

https://example.com/shared-item
```

Kinds reflect known content such as `text`, `caption`, `voice` or `location`. `forwarded` is added
only for explicit Telegram provenance; no heuristic guesses which segment is personal commentary.

Telegram sends a message as plain text plus a list of entities, and the plain text alone drops the
destination of every hyperlinked phrase. Segments therefore render `text_link` entities back as
ordinary Markdown links, so a forwarded post whose text reads `Paper Github Video` keeps all three
destinations. Entity offsets are counted in UTF-16 code units, and a label spanning a line break is
left as plain text (link discovery still records its destination). Other formatting entities such as
bold, italic and code are deliberately ignored.

## Voice-note transcription

A voice item is detected from an attachment-manifest entry whose `kind` is `voice`. Generic audio
uploads, videos and video notes do not trigger this step. Every downloaded voice attachment in a
grouped item is transcribed in attachment order.

The step reuses the local transcription implementation used for Instagram video audio. Backend,
model, cache directory and CPU threads come from the `voice-transcription` entry in `config.yaml`.
The shipped configuration uses the image-bundled multilingual `faster-whisper` small model with
CPU/int8 inference. Runtime model downloads are disabled in the container, so changing the model
requires rebuilding the image. The model choice is measured in
[`experiments/transcription-models/`](../../experiments/transcription-models/README.md).

Language is detected automatically, speech stays in its original language, voice-activity detection
is enabled to suppress silence and music, and no timestamps are added. Each transcript is rendered
in the segment of the message it came from; a captioned voice message keeps the transcript and
caption together. If no speech is detected, the item is delivered with:

```markdown
## Segment 1 — voice

[No speech recognized]
```

A missing attachment, an invalid media file, a file without an audio stream or a transcription error
is a declared failed outcome. The step is rolled back, later steps continue, and the item is
delivered with its original attachment so it can be played manually.

No separate transcript file is generated. The transcript is in `capture/source.md` and
`capture/message.md`, and the voice file stays under `capture/attachments/`.

## Text cleaning

Text cleaning reuses `clean_text` with its internal link resolution disabled. It normalizes
social-media formatting, strips invisible characters and look-alike letters, and removes tracking
parameters from links already present in the body. Segment headings are protected while it runs.

## Link discovery

Link discovery builds one ordered table of every distinct target the item carries, and writes it to
`links.json` at the item root. It never uses the network.

Links are collected from the retained Telegram entities first — both the visible `url` type and the
`text_link` type whose destination the plain message text drops — and then from the Markdown links
and bare URLs in the cleaned body, which covers voice transcripts and anything the entities missed.
Each link is unwrapped when an outbound redirector carries its destination in the URL itself, then
canonicalized: the scheme and host are lower-cased, a default port is dropped, and
sender-identifying parameters (`utm_*`, `igsh`, `si`, `is`, `fbclid`, `rcm`, `cp_landing*`) are
removed. Paths and fragments are left alone. Rows are deduplicated on the canonical URL, so one
target is one row however many times it appears.

Some links are not part of the item at all and are excluded outright, with the reason recorded so a
surprising row can still be traced. A Telegram channel, invite or web-preview link (`t.me/<channel>`,
`t.me/+invite`, `t.me/joinchat/…`, `t.me/s/<channel>`) is about the channel a post arrived from,
never about the item; a link to one post inside a channel is content and is kept. A bare domain
named in prose is excluded too: Telegram marks `BIKEPACKING.com` in pasted text as a link entity
even though the owner never shared it. Excluded rows stay in `links.json` and never reach `index.md`.

Every row records the position `n`, the raw and canonical URLs, the anchor text when the link came
from an entity, the segment it came from, the handler that would extract it, the handler's target
identity, its extraction priority, a status, and — once extraction has run — what extraction made of
it. Research providers rank highest, then the item's only link, then code repositories, then
articles and documents, and finally links that need a nested extraction. Profile roots, channels,
hashtag pages, image CDNs and store links are ranked last: they are worth a title and never worth
extracting.

Handlers and canonical URLs are provisional for shorteners, which cannot be followed without a
request. URL resolution corrects both.

## URL and title resolution

The step resolves the table in priority order, up to the configured `resolve_budget` (40), and
records the outcome on each row: `resolved` with a title, `unresolved` with a stable reason,
`skipped` when the budget is spent, or `duplicate` when two links turn out to share one destination.
Nothing is dropped. Budget exhaustion is normal operation and is not reported as a problem.

It also rewrites the body so it stays readable: bare HTTP(S) URLs, Markdown autolinks, and inline
links whose visible label is exactly their destination become `[page title](final URL)` when a
trustworthy title is available. Existing links such as `[descriptive title](https://example.com)` —
including the ones restored from Telegram entities — are preserved byte for byte and are not
fetched. URLs in code, image links, raw HTML attributes and Markdown reference definitions are not
title-enriched. Body rewriting shares the same budget.

Titles come from bounded public HTML metadata (`og:title`, Twitter metadata, Article/WebPage
JSON-LD, `<title>`, then `<h1>`). PDFs prefer document/XMP metadata and otherwise infer a title from
the first page's prominent text; file names and body text are not used. HTML and PDF downloads are
each capped at 20 MiB by the shipped configuration. Every redirect hop must remain public HTTP(S);
credential-bearing and private-network destinations are rejected. When an ordinary request meets a
block or an unusable response, the resolver retries once anonymously with the Chrome-compatible HTTP
client the Medium extractor uses. It does not run a browser, execute JavaScript or authenticate.
What those fallbacks bought is measured in
[`experiments/url-title-audit/`](../../experiments/url-title-audit/README.md).

If a destination resolves but has no trustworthy title, the final URL remains as bare text. Request,
redirect, content-type, size, safety and missing-title problems produce stable partial reasons;
usable output is retained.

Because resolution runs after cleaning, tracking parameters on a resolved destination survive in
`capture/message.md`, while `links.json` carries the canonical form.

The standalone `info-triage-resolve-urls` command performs title enrichment by default.
`--urls-only` restores redirect-only output, `--cache` persists final URLs and successful titles,
and `--report` writes a per-URL JSON audit. `--max-html-bytes` and `--max-pdf-bytes` match the step's
limits; `--strict` exits nonzero after writing output when any attempted enrichment had an expected
problem.

## Content extraction

Content extraction is the only step that retrieves bodies. It takes the resolved link table, works
down it in priority order, and writes one `extracted/NN-<handler>-<identity>/` directory per source
into the item. The handlers are documented in [`docs/extractors/`](../extractors/routing.md).

Two budgets bound it. `extract_budget` (5) links get a full extraction, dropping to
`linklist_extract_budget` (2) when the item carries at least `processing.linklist_threshold` (8)
distinct links — a curated list of 43 resources is filed as a list of titles, and 43 extracted
bodies would cost a great deal without changing that decision. Rows that are excluded, duplicates,
or ranked title-only never spend the budget. A per-item `wall_clock_seconds` ceiling (600) is checked
before each extraction; on exceeding it the remaining links stay title-only and the item reports
`wall-clock-exceeded`.

The threshold is one global value shared with `index-render`, so the budget applied and the reported
`kind` cannot disagree. Both count only the links the item arrived with.

### Following what an extraction points at

A retrieved page may point at the thing that was actually shared. A LinkedIn post announcing a paper
is transport for the paper, and on that platform the link is regularly in the author's own first
comment. So a finished extraction is asked what it points at, and those links are extracted in a
second pass out of the same budget and the same wall clock:

| Handler | Links followed |
|---|---|
| linkedin | the post body, plus the comments the post's own author left; when the author left none, the first comment carrying a link off LinkedIn |
| youtube | the description, capped at three |
| research, medium, document, instagram | none |

A paper's bibliography, a Medium author's back catalogue and a web page's navigation are not what
was saved, so those handlers follow nothing. **The depth is exactly one and is structural, not a
setting**: a link found this way is retrieved but is never itself asked what it points at.

Followed links join the item's link table with `origin: harvest` and a `via` naming where they were
found, so `index.md` can say why a source the item was never sent is part of it. They are ranked
like any other link, which is what promotes the item to what it turned out to be. They do not count
towards `link_count` or the link-list threshold: whether an item is a reading list is a fact about
what arrived. Links that would never earn extraction anyway — sponsorship, merchandise, social
profiles, channel self-references — are dropped rather than recorded.

### Output, cache and failures

Every extraction directory has the same shape; see
[`item-contract.md`](item-contract.md#the-item-directory). Setting `keep_raw: false` drops `raw/`.

Results are cached outside the item, under `data/extraction-cache/`, keyed on the canonical URL.
This is a requirement, not an optimization: an item is rebuilt from `payload.json` on every edit, so
without the cache adding a note to a message would download its paper again. A cache entry's
manifest is written last, so an interrupted extraction is re-run rather than served half-finished.
What the item keeps is a copy, because a later retrieval is free to replace the cache entry.

**No extraction failure ever blocks an item.** A refusal, a timeout, an unparseable page or a
missing extractor is recorded against its link and reported as a partial step; the item still lands
with its capture text, its link table and whatever else was retrieved.

Medium and Instagram retrieve substantially more when given a session.
`extractors.medium.cookie_file` and `extractors.instagram.session_file` / `cookies_file` are unset
by default; both are account credentials and belong in a mounted secret, never in the repository.
Without them a paywalled Medium article extracts as `partial` with reason `medium-member-preview`,
or `blocked` when the site refuses outright.

## Index rendering

Index rendering runs last and writes `index.md` at the item root. It uses no network and adds no
information of its own: everything it writes is already known from the retained payloads, the
processed body, the link table and the extraction records. The file's shape, fields and intent
detection are specified in [`item-contract.md`](item-contract.md#indexmd); this section covers what
the step decides.

It never declares `failed`. A rolled-back index would leave a ready item the sync refuses, holding
up every other item, so unreadable input is reported as `partial` and the field is omitted.

### The lead

`## Lead` quotes the top-priority source, in one of three ways, and says which in the `lead` field:

| `lead:` | When | What |
|---|---|---|
| `abstract` | the source is a paper | its complete abstract, never truncated |
| `full` | short-form media, everything fits | every stream, labelled, quoted whole |
| `excerpt` | anything else | a prefix, cut at a paragraph boundary and marked `…` |

**Short-form media** — every Instagram post, and YouTube videos whose metadata says `short` — is
quoted stream by stream within `media_lead_words` (800), because its payload is not in prose.
Streams are ordered by what only the pipeline could recover: forwarded commentary, then on-screen
text, then spoken audio or the transcript, and the caption or description last. Each is labelled in
bold, and the budget is shared fairly, not first come first served: every stream gets an equal
share, then whatever the short ones did not need goes to the long ones in priority order. A Short's
burned-in subtitles make its on-screen text nearly as long as its transcript, so spending the budget
in order would leave nothing for the description, often the only stream carrying anything the other
two do not.

That ordering is measured. A reel's caption is usually marketing while the audio carries the
argument; a Short's description is usually a hook while the transcript carries the content. A flat
prefix of `content.md` reaches neither, because the file is written caption-first. Over 14 real
Shorts and Reels, 800 words quotes 12 of them whole (median 183 words, 90th percentile 681).

Everything else keeps the first `lead_words` (120) of `content.md`. Forwarded material is quoted
because it is the only place that text appears outside `capture/`.

Bodies are sliced structurally: the H1, then the fact block, then the `## ` sections. A rule that
filters lines by how they start is not used, because one that skipped `- ` to drop the fact block
also deleted every bulleted list in every body.

### Problems

`## Problems` lists each problem as its step, outcome, reason key, target and message, with a
`problems` count in the frontmatter. A clean run is `succeeded`; a completed run with recoverable
target-level issues is `partial`; a declared `failed` run has its changes discarded before the next
step, and an unexpected exception is discarded the same way with its type and traceback logged. In
every case the item is delivered with its capture text, whatever earlier steps produced, and this
section naming what went wrong.

How runs are logged and counted is described in [`overview.md`](overview.md#telemetry).

## Revisions and existing items

Enabling a step does not scan, move or rewrite existing ready items. New captures use the current
catalogue. If an older item later receives a Telegram edit, that new revision goes through the
current pipeline. Each revision is reconstructed from the retained payload, not from a previously
processed `capture/message.md`, and revision checks prevent a slow result from overwriting a newer
edit.

## Adding a step

Three coordinated edits: a dataclass and parser case in `config.py`, the step class in
`preprocessing.py`, and a branch in `preprocessing.py:processing_steps_for_route()`. Every step goes
through the shared worker, so its runs are logged and counted centrally; a step does not add logging
of its own.

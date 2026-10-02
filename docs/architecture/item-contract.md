# The item contract

What a captured item looks like on disk, what its `index.md` says, and why it is shaped that way.
`index.md` is the only per-item file the laptop side reads, so this document is the interface
between the server pipeline and the agent that later files the item.

The measurements behind these decisions are in
[`experiments/item-contract/`](../../experiments/item-contract/README.md).

## What the contract is for

The consumer is a routing session: an agent that takes each item through four questions and files
it into the owner's plans. The contract is not "represent what the extractors produced". It is:

> **Answer in advance the questions the routing agent asks, and make everything else addressable
> but unread.**

| The agent asks | It needs | Field |
|---|---|---|
| Is this still current? | the item's date | `published` |
| How good is the source? | author, venue, primary or secondary, who forwarded it | `authors`, `kind`, `via` |
| Could it be retrieved at all? | whether the link resolved and returned content | `extraction` |
| Do I already have this? | canonical URL, title, topic | `canonical_url`, `title`, the lead |
| What is distinctive about it? | what would not be true of a neighbouring artifact | the lead, never a summary |
| Where does it belong? | subject matter | title and lead |
| Why was it saved? | the owner's own words | `intent` |

Two rules follow, and they settle most smaller arguments:

- **A field no question consumes does not belong in the index.** View counts, like counts and
  thumbnail lists stay in the extraction's own `metadata.json`.
- **Anything a question might need is one read away with its cost printed.** A path with no size
  forces a choice between reading twenty thousand tokens and reading nothing.

The owner reads these files too, reviews items himself and sometimes processes one by hand, so the
output stays readable by a person.

## The item directory

```text
2026-08-11_100/
├── index.md                the contract; the only file the laptop side has to read
├── metadata.json           identity, revision, attachment manifest
├── links.json              the ordered link table
├── capture/                provenance: what arrived and what the pipeline made of it
│   ├── source.md           segments before any transformation
│   ├── message.md          the transformed body
│   ├── payload.json        the complete received payloads
│   └── attachments/        retained media
└── extracted/              what was retrieved from the item's links
    ├── 01-linkedin-7492274768650407936/
    │   ├── content.md      the body
    │   ├── comments.md     where the handler has comments
    │   ├── metadata.json   the handler's own fields
    │   ├── status.json     complete | partial | blocked | failed, with a reason key
    │   └── raw/            everything retrieved but not converted
    └── 02-research-arxiv-2607.12345/
```

It is shaped like a small source tree on purpose, because that is a shape a code-trained reader
already knows how to navigate: read the contract first, open a body when a decision needs it, never
open the build output. `capture/` and every `raw/` directory are that build output. They are kept so
a person can inspect what was retrieved, and nothing in the routing path reads them.

- **Self-contained.** One item can be handled on its own, and deleting its directory is the unit of
  work that marks it processed. Extracted content lives inside the item and not in a separate store,
  so the whole pipeline could run on another machine without splitting anything.
- **Uniform.** Every extraction directory has the same names whichever handler produced it, so a
  reader learns one convention. The names live in `extractors/artifacts.py`.
- **Paths are item-relative.** Attachment paths in `metadata.json` begin `capture/`. Generated output
  may not land inside `capture/`, which belongs to the capture layer.

`capture/source.md` and `capture/message.md` carry one ordered `## Segment N — <kind>` section per
Telegram message. Segments are a transport detail (sharing a link and typing a comment makes two
messages), so they never appear in `index.md`.

## `index.md`

Written by the last pipeline step. A plain note is a handful of lines; a paper reached through a
social post is about thirty.

```markdown
---
id: 2026-08-11_100
captured_at: 2026-08-11T14:33:28Z
origin: linkedin
intent: "new research on agent harnesses"
kind: paper
title: "Agent Harnesses for Long-Horizon Tool Use"
authors: [A. Author, B. Author]
published: 2026-07-24
venue: arXiv
canonical_url: https://arxiv.org/abs/2607.12345
extraction: ok
sources: 2
lead: abstract
---

## Captured

> new research on agent harnesses

## Sources

1. [extracted/02-research-arxiv-2607.12345/content.md](extracted/02-research-arxiv-2607.12345/content.md) — Agent Harnesses for Long-Horizon Tool Use · A. Author, B. Author · 2026-07-24 · arXiv — complete · 11,900 words · [pdf](extracted/02-research-arxiv-2607.12345/raw/paper.pdf) · via 01 · author comment
2. [extracted/01-linkedin-7492274768650407936/content.md](extracted/01-linkedin-7492274768650407936/content.md) — 2026-08-11 — complete · 310 words

## Lead

> We study agent harnesses for long-horizon tool use… [the complete abstract]

## Links

1. [A post about agent harnesses](https://www.linkedin.com/posts/…) — linkedin · resolved
2. [Agent Harnesses for Long-Horizon Tool Use](https://arxiv.org/abs/2607.12345) — research · resolved
```

### Frontmatter

The frontmatter is the machine-readable part and is authoritative. **A field is present only when it
is actually known.** An absent field is information; an invented one would be trusted and acted on.

| Field | Present | Source |
|---|---|---|
| `id` | always | the directory name |
| `captured_at` | when readable | `metadata.json`, in UTC |
| `origin` | always | `telegram` for a forward, else `instagram｜linkedin｜medium｜youtube｜web` from the primary link, else `voice` or `text` |
| `via` | when forwarded | Telegram's forwarding provenance, such as `@channel (forwarded channel)` |
| `intent` | always, `null` when undetected | the three heuristics below |
| `kind` | when known | `note` with no links, `linklist` at the link-list threshold, otherwise what the top extraction turned out to be: `paper｜article｜pdf｜repo｜video｜post` |
| `title` | when the top source has one | extraction metadata |
| `headline` | when there is no title | derived, see below |
| `authors`, `published`, `venue`, `doi` | when known | the top source's metadata |
| `canonical_url` | when the item has a link | the highest-priority row of the link table |
| `extraction` | always | `ok｜partial｜failed｜none` |
| `reason` | when `extraction` is not `ok` and a reason exists | the stable reason key |
| `sources` | when anything was extracted | the number of extraction directories |
| `lead` | when a lead is quoted | `abstract｜full｜excerpt` |
| `link_count` | above five distinct links | the links the item arrived with |
| `problems` | when preprocessing hit any | their count |

`title` is copied from the source. `headline` replaces it, never accompanies it, when the source has
none, which is most items: an Instagram post has no title worth the name, and without a derived
label a queue shows a date and a kind and no way to tell one item from the next. It is the only
field derived for legibility, so it is computed on the server, where the caption, the on-screen text
and the transcript are still in reach. A consumer reads `title or headline`.

`kind` is promoted to what the item turned out to be. A LinkedIn post whose author's comment links
an arXiv paper is `kind: paper`, and its lead is the abstract, because the post is transport for the
paper.

`extraction` is never hidden, because it changes what may be concluded. A Medium member preview is
`partial` with `medium-member-preview`: enough to identify and file the article, not enough to judge
whether its claim holds up.

### Sections

- **`## Captured`** quotes the owner's own words and nothing else, in whatever language they were
  written. A transcript is marked as dictated, so a garbled phrase reads as a recognition artifact.
  When the item is only a note, this is the whole file.
- **`## Sources`** lists every extraction with **the word count of its body**. That count is the most
  important affordance in the file: it turns opening a body into a costed choice between a 200-word
  abstract and an 11,900-word paper. Where a paper's PDF was kept it is linked too, for the reader.
- **`## Lead`** quotes the top-priority source: a paper's complete abstract, every stream of a
  short-form video within a budget, or a truncated opening marked `…`. The `lead` field says which,
  and therefore whether the quote stands on its own. The rules are in
  [`preprocessing.md`](preprocessing.md#index-rendering).
- **`## Links`** is the resolved link table, one row per distinct target. It grows to dozens of rows
  for a link list and disappears for a voice note. Rows the item does not own stay in `links.json`
  with `status: excluded` and never reach the index.
- **`## Problems`** appears only when preprocessing hit some, and comes last. It turns a missing
  title or a thin `## Sources` from a mystery into a fact about the run.

`## Sources` and `## Links` are lists, never Markdown tables. A table is as wide as its widest row,
these rows carry page titles, and the file is read in a half-width editor window beside the agent. A
list re-flows at any width and costs fewer tokens.

### Detecting intent

`intent` is the highest-value field in the file: the owner's reason for saving something carries
more signal than the content does. It is quoted verbatim and detected without a language model, by
three positional rules over the segments:

| Rule | Example |
|---|---|
| Short text after the last link in the same message is the note | `…/reel/DbW0FoHI1OO/ Try loops` |
| A short, link-free, non-forwarded message beside a forwarded or link-bearing one is the note | a reel, then "Interesting thought to ponder upon" |
| A forwarded message is never the note | Telegram's forwarding provenance is authoritative |

When the rules find no candidate or more than one, `intent` is `null` and everything the owner
wrote stays in `## Captured`. An empty field is honest; a wrongly attributed one is worse than none,
because it will be trusted.

## What each handler contributes

| Handler | In the index | Follows links from | Never in the index |
|---|---|---|---|
| research | title, authors, date, venue, DOI, the full abstract | nothing | the body, source HTML |
| document (HTML) | title, author, date, an excerpt | nothing | the body, source HTML |
| document (PDF) | title, an excerpt | nothing | the body, the source PDF |
| medium | title, author, date, an excerpt; `partial` on a member preview | nothing | the body, the response HTML |
| linkedin | author, date, an excerpt | the post, plus the author's own comments | other people's comments, media |
| youtube | title, channel, date, description and transcript | the description, capped at three | the transcript body, comments, media |
| instagram | owner, date, on-screen text, spoken audio, caption | nothing | every comment, media, per-frame OCR |

The comment policy follows the measurements. On LinkedIn the paper is regularly in the author's own
first comment while the rest of the thread is filler, so the author's comments lead. Instagram
comments were about 38% of a post's text and none of its signal, so they are kept on disk and never
quoted. Following links is described in [`preprocessing.md`](preprocessing.md#content-extraction).

## Worked examples

### A note

A voice message with no links. Nothing is retrieved.

```markdown
---
id: 2026-08-12_111
captured_at: 2026-08-12T20:54:53Z
origin: voice
intent: null
kind: note
headline: "Интересно, как он справится с распознаванием разных языков."
extraction: none
---

## Captured

> Интересно, как он справится с распознаванием разных языков. Например, сейчас
> я говорю по-русски и сможет ли он распознать русский язык, интересно
>
> — transcribed from a Telegram voice message
```

The note stays in the language it was dictated in.

### Short-form media with a comment

A reel shared with "Interesting thought to ponder upon. Write and reflect about it". The intent does
real work here: "write and reflect" points at a destination the reel's content alone would not.

```markdown
---
origin: instagram
intent: "Interesting thought to ponder upon. Write and reflect about it"
kind: post
headline: "<first line of the on-screen text>"
published: 2026-08-07
canonical_url: https://www.instagram.com/reel/DYjm3g7Onqg/
extraction: ok
sources: 1
lead: full
---

## Lead

> **On-screen text** …
>
> **Spoken audio** …
>
> **Caption** …
```

A reel's caption is marketing and its audio is the argument, so the recovered streams come first and
each is labelled.

### A post that is transport for a paper

The example at the top of this document. Four things happen at once: the paper found in the author's
comment is retrieved, `kind` becomes `paper`, the lead is the paper's abstract and not the post's
prose, and the other comments are left in `comments.md`.

### A link list

A post linking 43 resources. At the link-list threshold (eight distinct links) the item is read as a
list of titles, and at most two links are extracted.

```markdown
---
origin: telegram
intent: null
kind: linklist
headline: "I invested almost 3 hours picking the best resources for 43 system design concepts."
extraction: ok
link_count: 43
---

## Links

1. [Throughput vs Latency](https://aws.amazon.com/compare/…) — document · resolved
2. [The CAP Theorem](https://www.bmc.com/blogs/cap-theorem/) — document · resolved
…
```

Extracting little is correct here. The routing outcome for a pile of links is one task plus one
reference note, and the contents of link 27 cannot change it. Forty-three resolved titles cost about
600 tokens; forty-three extracted bodies would cost about 107,000 and be discarded.

## Why nothing is summarized

No language model runs anywhere in the extraction path. Extractors retrieve and convert.

1. **A summary destroys what makes an item distinctive.** A summary is written to be representative:
   it keeps the gist and deletes the idiosyncrasy. Three summarized blog posts read as three
   interchangeable paragraphs, and the detail that would make a good task is the first casualty.
2. **It destroys what source quality is judged on.** Whether an author shows their working or
   recycles a press release is legible in the prose. A summary launders a listicle into something
   that reads like a paper.
3. **Truncation is honest and a summary is not.** A lead cut at 120 words is visibly a fragment, so
   a reader who needs more knows to open the body. A summary looks complete, so the reader stops
   there, and that failure is silent.
4. **It would add a failure mode to a path whose whole value is reliability.**

## Why Markdown, with a thin Org view

The contract and all content are Markdown. The deciding argument is escaping: arbitrary extracted
web text is not safe to embed in an Org file. A leading `*` becomes a heading, `_foo_` becomes
underline, `[[` opens a link, and every extracted lead would have to pass through an escaper whose
bugs corrupt items. The extractors emit Markdown, so a lead quoted from `content.md` is already
valid. YAML frontmatter is also the idiom a code-trained model handles most reliably.

Org is still the right tool for reviewing a queue, so the sync generates `triage.org` as navigation
only: a heading per item, with a one-line label and links. See [`sync.md`](sync.md).

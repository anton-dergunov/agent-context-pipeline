# Message contract design — extractor wiring and item representation

Written 2026-08-14. Design proposal only; nothing in this repository or in the org
repo was modified. It answers two questions:

- **A. Wiring** — in what order do the extractors run, when do they recurse, and
  what are the budgets?
- **B. Representation** — what does an item directory look like, what does the
  generated index look like, and what exactly does `/route` read?

Everything below is grounded in measurements of your own material, listed in §1.

---

## 0. Recommendations at a glance

| # | Question | Recommendation |
|---|---|---|
| 1 | What shapes the contract? | The four `/route` lenses. Every field must answer `vet`, `dup`, `task` or `destination`, or it goes. |
| 2 | One file or a directory for `/route`? | **One file per item (`index.md`), concatenated into one generated `triage.md`.** `/route` reads `triage.md` and nothing else unless a lens needs a body. |
| 3 | Markdown or Org? | **Markdown for the contract and all content. A thin generated `triage.org` as the Emacs navigation layer.** Reasons in §4.4 — the decisive one is that arbitrary extracted text is not safe to embed in Org. |
| 4 | Full bodies in the index? | **No. Abstract (papers) or first ~120 words (everything else), plus a word count and a path.** Layered, addressable, with the cost printed. |
| 5 | Summarize with an intermediate LLM? | **No.** It destroys exactly what `vet` and `task` need, and truncation is honest where summary is not. §3.6. |
| 6 | Recursion depth | **One level, handler-specific, never deeper.** §3.4. |
| 7 | How many links to extract? | **Two budgets: full extraction for ≤5 ranked URLs; title-only resolution for up to 40 more.** A link list routes as a link list. §3.3. |
| 8 | Comments | **Author's own comments only, and only for LinkedIn and YouTube.** Instagram comments never reach the index. Saves ~38% of Instagram tokens. §3.4. |
| 9 | Segments | **Keep in `capture/message.md`, remove from the index.** Replace with one `intent:` field plus a "What was captured" section. §4.5. |
| 10 | Failure | **`extraction:` status is a first-class frontmatter field and is never hidden.** A failed fetch is information `/route` must act on, not a gap to paper over. |
| 11 | `/route` changes | **~15 lines.** One scope clause, one contract paragraph, one `vet` amendment. §6. |

Expected cost of a 20-item day: **~6k tokens for the whole digest**, versus
30–60k tokens of failing web round-trips today. Derivation in §4.6.

---

## 1. What I measured

### 1.1 Where `/route` actually spends its budget

I parsed all 90 session transcripts in
`~/.claude/projects/<org-repo>/`
(77 of them mention routing). Tool-call totals across all sessions:

| Tool | Calls |
|---|---|
| Edit | 1650 |
| Bash | 1443 |
| Read | 765 |
| **WebSearch** | **385** |
| Write | 375 |
| **WebFetch** | **223** |

Three findings matter for this design.

**Finding 1 — search outnumbers fetch 1.7 : 1.** The expensive part of routing is
not "download this page", it is "what *is* this thing, and is it still current".
203 of the 385 search queries contain the string `2026`. A representative sample:

```
Pocket read-later app shut down Mozilla status 2026
Gaia GPS Apple Watch app discontinued 2026 WorkOutDoors watch offline maps hiking
Lottery Ticket Hypothesis still relevant 2025 pruning large language models research status
graph foundation models 2025 2026 state of the art what generalizes across graphs
"DeepMind x UCL RL Lecture Series" 2021 youtube.com/playlist?list=
Vipra Singh "Building LLM Applications" series Medium list index
```

These split into two kinds, and the distinction drives the whole design:

- **Identification** ("which playlist is this", "which Medium series", "who wrote
  it, when") — *this is fully pre-computable* and is pure waste at routing time.
  The DeepMind/UCL playlist alone cost three consecutive searches.
- **Currency** ("has this been superseded") — *this is not pre-computable*; it
  needs the live web and it needs your judgement. Pre-extraction does not remove
  it, but it converts an exploratory search into one targeted search, because the
  agent now starts with title, author and date instead of a bare URL.

Do not oversell this to yourself: **enrichment removes the retrieval and
identification cost, and sharpens but does not remove the currency check.**

**Finding 2 — one WebFetch in ten fails outright.** 21 of 223 (9.4%) returned a
hard error, dominated by 403:

```
403  https://openai.com/index/learning-from-human-preferences/
403  https://www.oreilly.com/library/view/15-math-concepts/9781837634187/
403  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4722115
403  https://www.mitpressjournals.org/doi/full/10.1162/tacl_a_00279
403  https://content.dataiku.com/nurturing-drift-detectors/oreilly-interpretability
404  https://klang.io/pricing/
410  https://www.rightmove.co.uk/properties/131380418
timeout  https://helpx.adobe.com/lightroom-classic/help/hdr-photo-merge.html
ENOTFOUND  https://blog.usejournal.com/…, onlinehub.stanford.edu, guide.allennlp.org
cert expired  http://www.tangoandchaos.ru/
```

Note what is in that list: publishers, journals, and course platforms — exactly
the sources `vet` is supposed to judge on quality. The lens that most needs the
artifact is the one most often denied it. Across the whole corpus, `403`,
`paywall` and `unverified` appear in 179, 102 and 76 transcript messages
respectively, and `lnkd.in` in 52.

**Finding 3 — the failures are silent-ish.** A 403 does not stop the session; it
produces `unverified` findings, which is correct behaviour per `lenses.md` but is
a degraded routing decision. Your extractors already fix most of this. The
contract's job is to make the fix *visible* so `/route` knows which findings rest
on retrieved evidence and which do not.

### 1.2 What the extractors already produce

Measured from the output directories in this repository.

**`url_output/summary.json`** — a 97-URL corpus drawn from your own notes:

| | |
|---|---|
| attempted | 97 |
| complete | 86 (**89%**) |
| failed | 11 |
| by group | html 50, pdf 14, research 33 |

Failure reasons: `metadata-unavailable` 3, `access-blocked` 2,
`html-no-extractable-text` 2, `request-error` 2, `http-error` 1, `unsafe-url` 1.

So on a corpus of the same kind of URLs that made `/route` sessions fail, the
server retrieves 89% of them — and enumerates the residual 11% with stable reason
keys instead of leaving a silent gap. That residue is what the `extraction:`
field exists to expose.

**Body sizes** — the reason layering is mandatory, not optional:

| Artifact | Size | Tokens (≈) |
|---|---|---|
| `research/arxiv-2410.04840/metadata.json` abstract | 1.7 KB | 420 |
| `research/arxiv-2410.04840/paper.md` | 80 KB / 11,931 words | ~20,000 |
| `html/amitness-com-simclr/content.md` | 10 KB / 1,458 words | ~2,600 |
| `pdf/…the-illusion-of-thinking/content.md` | 82 KB / 12,186 words | ~20,500 |
| `youtube/ztdTed5egrM/llm_input.txt` (RL lecture, 77 min) | 68 KB | ~17,000 |
| `youtube/RCIEEQxRer8/llm_input.txt` (26 s Short) | 1.7 KB | ~430 |
| `medium/dc2e9c207d00/article.md` | 9.7 KB / 1,544 words | ~2,400 |
| `linkedin/…/llm_input.txt` (5 posts) | 1.1–5.6 KB | 270–1,400 |

Twenty items at full body would be 100k–300k tokens. Twenty items at
abstract-plus-metadata is single-digit thousands. There is no third option.

**Composition of an Instagram extraction** (11 posts in
`~/projects/archive/text-cleanup/instagram_output/`):

| Part | Bytes | Share |
|---|---|---|
| caption | 9,743 | 13% |
| **comments** | **28,502** | **38%** |
| OCR text | 31,416 | 42% |
| `llm_input.txt` total | 74,660 | |

The comments are 38% of the payload and are, for Instagram, almost pure noise —
here is a real sample from a tango post:

```
[3 likes] @kris.tian.e: Finesse!!! 🔥❤🙌
[3 likes] @tangofisher: 👏👏👏👏
[2 likes] @vwerauma: 😍😍😍
```

Your intuition is confirmed by the bytes: **drop Instagram comments from the
index.** The OCR text, by contrast, *is* the content of a reel and must stay.

**LinkedIn is the opposite case**, and this is the single best worked example in
the corpus. `linkedin_output/7487448227336716288/llm_input.txt` — a Maxime
Labonne post about "BabelTele". The post body is 1,700 characters of readable
commentary. Then:

```
FIRST PUBLICLY RETURNED COMMENT
Maxime Labonne: 📝 Paper: https://arxiv.org/pdf/2606.19857
```

...followed by nine comments from other people, several of which are visibly
LLM-generated engagement filler. **The author's own first comment carries the
paper. Every other comment is noise.** That is a precise, implementable rule, not
a vague heuristic — see §3.4.

### 1.3 What the current inbox looks like

39 items in `~/info-triage-inbox/`, `triage.md` at 539 lines. Concrete defects the
new contract must fix:

1. **Bodies are pasted whole into the index.** Item `2026-08-09_77`
   (Afisha.London weekly digest) occupies 47 lines of `triage.md` and is a
   newsletter table of contents. At 20 items/day this file becomes the thing you
   stop opening.
2. **Segment heading levels are inconsistent** (`## Segment 1` in item 116,
   `### Segment 1` in item 118, absent in older items) — a real bug, and one that
   disappears entirely if segments leave the index (§4.5).
3. **Hyperlinks are silently lost.** I checked `telegram.json` across all items:
   item `2026-08-09_77` carries two `text_link` entities pointing at
   `http://eepurl.com/gN1Fhvw2ji` that appear **nowhere** in `message.md`,
   because Telegram's plain text drops the href of hyperlinked text. Item
   `2026-08-13_115` shows the visible symptom — `*Diffusion explainer *Github
   *Статья *Видео` — four link labels with no links. **URL discovery must read
   `entities` and `caption_entities`, not regex the text.** This is currently a
   silent data-loss bug.
4. **Forwarding provenance is captured but unused.** `forward_origin` gives you
   `{"title": "Machinelearning", "username": "ai_machinelearning_big_data"}` and
   the original post date. That is a `vet` source-quality signal (aggregator
   channel ⇒ secondary source) available for free.
5. **URL resolution already works.** Item `2026-08-13_113`: `source.md` has four
   `lnkd.in/…` links, `message.md` has the four real destinations. This is the
   single highest-value thing already shipped.

---

## 2. The design brief, restated

The contract is not "represent what the extractors produced". It is:

> **Pre-answer, in the order they are asked, the questions the four lenses ask —
> and make everything else addressable but unread.**

| Lens | What it needs | Where it comes from |
|---|---|---|
| `vet` — currency | the item's date | `published:` |
| `vet` — source quality | author, venue, primary-vs-secondary, forwarding chain | `authors:`, `kind:`, `via:` |
| `vet` — reachability | did this URL resolve and return content, *today* | `extraction:` |
| `dup` | canonical URL, title, topic | `canonical_url:`, `title:`, lead |
| `task` — distinctiveness | what would *not* be true of a neighbouring artifact | abstract / lead — **never a summary** |
| destination | subject matter | title + abstract |
| all of them | why you saved it | `intent:` — the highest-value field in the file |

Two corollaries fall straight out and are worth stating because they settle a lot
of smaller arguments:

- **A field that no lens consumes does not belong in the index.** View counts,
  like counts, thumbnail arrays, `aspect_ratio`, `channel_follower_count` — all
  present in `youtube/metadata.json`, none of them route anything. They stay in
  the extraction directory.
- **Anything a lens *might* need must be one `Read` away with its cost printed.**
  A path with no size hint forces the agent to choose between reading 20,000
  tokens and reading nothing.

---

## 3. Part A — Wiring the extractors

### 3.1 Pipeline order

Current `config.yaml`: `voice-transcription → url-resolution → text-cleaning`.
Proposed:

```
A. capture materialization
   1. voice-transcription      (existing) → capture/source.md
   2. text-cleaning            (existing, MOVED EARLIER)
   3. intent-detection         (new, heuristic, no LLM)

B. link normalization
   4. link-discovery           (new)  entities + text → canonical link table
   5. url-resolution           (existing, now table-driven, not text-driven)

C. content extraction
   6. content-extraction       (new)  route_url() → extracted/NN-<handler>-<id>/
   7. nested-extraction        (new)  depth 1, handler-specific harvest rules

D. render
   8. index-render             (new)  → index.md
```

Three ordering notes:

**Move `text-cleaning` before the URL work.** Today it runs last. It must run
before link discovery, because it strips zero-width characters, homoglyphs and
emoji that can glue themselves to a URL and defeat discovery. It already
preserves markdown links and already strips tracking parameters
(`clean_url()`), so running it first loses nothing. The existing
`SEGMENT_HEADING_RE` placeholder trick keeps headings intact; extend the same
protection to resolved `[title](url)` spans if you ever need to re-clean.

**Link discovery becomes a *table*, not an in-place text rewrite.** Today
`URLResolutionStep` rewrites the markdown body via `enrich_links()`. Keep that —
it is what makes `capture/message.md` readable — but the *pipeline* should also
emit a structured link table, because stages 6 and 7 need per-link state
(canonical URL, source segment, handler, priority, status) that cannot live in
prose.

**Extraction is idempotent and revision-keyed.** Per the existing
`(item_id, revision)` rule: an extraction directory whose canonical URL is
unchanged from the previous revision is reused, not re-fetched. Editing a
Telegram message to add a note must not re-download a 30-page PDF.

### 3.2 Stage B — link discovery

Sources of links, in order:

1. `entities` and `caption_entities` from each `telegram.json` payload — types
   `url` (visible) **and `text_link` (hidden href)**. This is the fix for
   defect §1.3.3.
2. Markdown links and bare URLs in the cleaned body (covers voice transcripts and
   anything the entities missed).

Then, per link:

- unwrap tracking redirectors that embed the destination (`embedded_destination()`
  already does this for LinkedIn's wrapper);
- resolve recognized shorteners — `lnkd.in`, `t.co`, `bit.ly`, `youtu.be`,
  `dpmd.ai`, `eepurl.com` (that last one appears in your own corpus);
- canonicalize: strip `utm_*`, `igsh`, `si`, `is`, `fbclid`, `rcm`,
  `cp_landing*` (item `2026-08-11_94` has a 9-parameter Fever ticket URL);
- **deduplicate on the canonical URL** — one extraction per distinct target even
  if it appears in both the capture and a nested harvest.

Output: an ordered link table with `{n, raw, canonical, from_segment, handler,
priority, status}`.

### 3.3 Stage C — extraction, with two budgets

This is where I differ from the "up to three links" plan, and I think the
difference matters.

> **`extract_budget` = 5** links get full extraction.
> **`resolve_budget` = 40** further links get title resolution only.
> If the item has **≥ 8** links, drop `extract_budget` to **2**.

Rationale, using your own item `2026-08-13_113` — the "43 system design concepts"
LinkedIn post:

- Full extraction of 43 links would produce roughly 43 × 2,500 = **107,000 tokens**
  of article markdown that nobody will ever read, and take an hour on the NAS.
- A **titled** list of 43 links costs about **600 tokens** and is *exactly* what
  `/route` needs — because `AGENTS.md` already tells it what to do with a link
  pile: *"A bare list of links is not a task… write the task, and move the links
  into the matching Obsidian note."* The routing decision is "one task + one
  vault note"; the contents of link #27 do not change it.

So the ≥8-link rule is not a cost compromise, it is the *correct* representation
for that class of item. The two budgets also handle the ordinary case cleanly:
a channel post with paper + code + demo has three links, all three get extracted.

**Priority ranking** for which links spend the `extract_budget` (document order
is a poor proxy):

1. research providers (arXiv, ACL, OpenReview, NeurIPS, PMLR, JMLR, CVF) — best
   value per byte: `metadata.json` alone gives title, authors, date, venue,
   subjects, DOI and a complete abstract;
2. the link the `intent:` text refers to, or the only link in the item;
3. GitHub / GitLab repositories — the README is precisely the artifact `vet`
   is told to open, and `api.github.com` also gives `pushed_at` and `archived`,
   which is a currency signal you cannot get any other way;
4. `medium`, `document` (HTML/PDF);
5. nested `youtube` / `instagram` / `linkedin`.

**Deprioritized to title-only, always:** social profile roots, hashtag links,
`t.me/<channel>` self-references, image CDNs, store/affiliate links
(`amzn.to`, `eepurl.com` signup links), and any host already extracted for this
item.

**Per-item wall-clock cap** (suggest 10 minutes). On exceeding it, remaining
links downgrade to title-only and the item is marked `extraction: partial`.
This is the two-lane backpressure point: a 77-minute video OCR must not stall
the text items behind it.

### 3.4 Stage D — recursion, depth 1, handler-specific

Recursion is not uniform, and treating it uniformly is how this explodes. The
harvest rules follow directly from the measurements in §1.2:

| Handler | Harvest links from | Why |
|---|---|---|
| **plain text / forwarded** | everything, subject to §3.3 budgets | The main case. Channel posts list paper + code + demo. |
| **linkedin** | post body **+ comments authored by the post author**; if the author left none, the first comment containing a non-LinkedIn URL | Measured: the paper lives in the author's own first comment (§1.2). Other comments are filler. |
| **youtube** | description links only, capped at 3, excluding social/affiliate/merch | Technical channels put the paper and repo in the description. |
| **research** | **none** | The paper is terminal. Its bibliography is not what you saved. |
| **medium** | **none** | Body links are overwhelmingly the author's own back-catalogue. |
| **document (html/pdf)** | **none** | A page's outbound links are unbounded and mostly navigation. |
| **instagram** | **none** | Instagram strips links from captions; there is nothing to harvest. |

**Depth is exactly 1.** A nested extraction never harvests. The bound is
structural, not a counter to tune — a LinkedIn post → the arXiv paper is the
whole journey; the paper's references are a different research task.

Every nested extraction records `via:` in its metadata (e.g.
`via: 01 · author comment`), which appears in the index's Sources list. Provenance
stays legible and costs one short line.

### 3.5 Failure semantics

Non-negotiable, and it matches what the codebase already does:

- **No extraction failure ever blocks an item.** Every item lands with, at
  minimum, its capture text and its link table.
- Every link carries a status from the existing vocabulary —
  `complete | partial | blocked | failed` — and, on failure, the existing stable
  reason key (`access-blocked`, `metadata-unavailable`, `html-no-extractable-text`,
  `http-error`, `unsafe-url`, `request-error`).
- These roll up to one item-level frontmatter field:
  `extraction: ok | partial | failed | none`.
- **`partial` must be distinguishable from `ok` in the index**, because it
  changes what `/route` may conclude. The Medium case is the sharpest: a
  `preview` article (title plus ~220 words behind the member wall) is enough to
  identify and route, and *not* enough to judge quality. It must say so:
  `extraction: partial` + `reason: medium-member-preview`.

### 3.6 Should an intermediate LLM summarize? No.

You asked directly, so here is a direct answer with the reasoning.

**Do not put an LLM in the extraction path.** Four reasons:

1. **It destroys the thing `task` needs.** `lenses.md` says the test for a good
   task is *"the exercise should be the thing that would not be true of a
   neighbouring artifact."* A summary is written to be *representative* — it
   preserves the gist and deletes the idiosyncrasy. Summarize three ML blog posts
   and you get three interchangeable paragraphs. The distinctive detail — the
   simulation harness the book ships, the two capabilities the tool combines — is
   the first casualty.
2. **It destroys the thing `vet` needs.** `vet` judges *source quality*, and
   quality is legible in the prose: whether the author shows their working, or is
   recycling a press release. A summary launders a listicle into something that
   reads like a paper. You would be deleting the signal you asked the extractor
   to go and get.
3. **Truncation is honest; summary is not.** A lead cut at 120 words is visibly a
   fragment — the agent knows to open `content.md` if it needs more. A summary
   *looks complete*, so the agent stops there and never drills down. The failure
   is silent, which is the worst kind.
4. **It adds a failure mode to a path whose entire value is reliability.** You
   built this because retrieval was unreliable. Do not put a non-deterministic
   step in front of the artifact.

This also confirms rather than reopens the conclusion in
`career-system/info-triage.md` §1.7: *"Summarise the metadata, title, author,
date — never replace the body."*

**Where an LLM call would actually pay, later, and only there:** grouping a
40-link reading list into 4–5 themes. Even that is probably unnecessary once the
links have resolved titles. Everything else on the list — intent detection,
kind classification, destination hints — is better served by a heuristic (§4.5)
or by embeddings (`near:`, deferred to §7).

---

## 4. Part B — Representation

### 4.1 Item directory layout

Shaped deliberately like a small source tree, because that is the shape a
code-trained agent already knows how to navigate: read the README, open `src/`
when you need to, never open `build/`.

```
2026-08-11_100/
  index.md                          ← THE CONTRACT. The only file /route must read.
  metadata.json                       item identity, revision, category  (existing)
  capture/
    message.md                        cleaned, link-enriched Telegram segments (existing)
    source.md                         pre-clean, pre-resolution original     (existing)
    telegram.json                     raw payload                            (existing)
    attachments/                      01-voice.ogg, 01-photo.jpg            (existing)
  extracted/
    01-linkedin-7492274768650407936/
      content.md                      post body — canonical entry point
      comments.md                     author comments first, then the rest
      metadata.json                   normalized card fields
      status.json                     complete | partial | blocked | failed
    02-arxiv-2607.12345/              via: 01 · author comment
      content.md                      abstract + full body
      metadata.json
      status.json
```

Three properties this buys:

- **Self-contained.** A single item can be routed on its own, and "delete the
  directory" remains the unit of work that drives NAS deletion. Do not break
  that; it is the best property the current design has.
- **Uniform.** `/route` learns *one* filename convention — `content.md` — not six
  extractor-specific ones (`paper.md`, `article.md`, `post.txt`, `llm_input.txt`).
- **Obvious what not to open.** `capture/` and `*.raw.*` read as provenance
  without needing a rule to say so.

**Each handler gets a thin adapter** (~10 lines) that maps its native output to
this shape:

| Handler | `content.md` is | Native files retained |
|---|---|---|
| research | `metadata.abstract` + `paper.md` | `paper-source.html`, `metadata-source.html` |
| document | `content.md` (already) | `source.html` / `source.pdf` |
| medium | `article.md` | `article.html`, `response.html` |
| linkedin | `post.txt` → markdown | `comments.json`, `media/` |
| youtube | title + description + `transcript.txt` + `ocr_text.txt` | `comments.*`, `media/`, `metadata_raw.json` |
| instagram | caption + `ocr_text.txt` + audio transcript | `comments.*`, `ocr/`, `media/` |

The adapter is also where the comment policy from §3.4 lives — it is the right
place for it, and it means the policy is one function, not a scattered rule.

**Deprecate `llm_input.txt` as an interface.** It was designed for a different
consumer: it bundles metadata, body, comments and OCR into one blob for a
summarizing model. That is precisely the wrong shape for a layered contract,
because it forces the whole payload on any reader. Keep generating it for
standalone CLI use; the pipeline should consume the parts.

### 4.2 The `index.md` contract

Fixed shape. Everything is optional-but-ordered, so a plain note is five lines
and a paper is thirty.

```markdown
---
id: 2026-08-11_100
captured_at: 2026-08-11T14:33:28Z
origin: linkedin
via: "@omarsar (LinkedIn)"
intent: "new research from Meta on agent harnesses"
kind: paper
title: "Agent Harnesses for Long-Horizon Tool Use"
authors: [A. Author, B. Author]
published: 2026-07-24
venue: arXiv
canonical_url: https://arxiv.org/abs/2607.12345
extraction: ok
sources: 2
---

`title:` is copied from the source; `headline:` replaces it — never accompanies
it — when the source has none. Most items are in that case: an Instagram post has
no `<title>` worth the name, and without a derived label the queue shows a date
and a kind and no way to tell one item from the next. It is the only field
derived for legibility rather than copied across, so it is computed here, where
the caption, the on-screen text and the transcript are all still in reach; by the
time the laptop renders the views it has only `index.md`. A consumer reads
`title or headline` and is never given both.

## Captured

> new research from Meta on agent harnesses

## Sources

1. `extracted/01-linkedin-7492274768650407936/` — LinkedIn post by Omar Sar,
   2026-08-11 — ok · `content.md` 310 words
2. `extracted/02-arxiv-2607.12345/` — arXiv 2607.12345, 2026-07-24 — ok ·
   `content.md` 11,900 words · via 01 (author comment)

## Lead

> We study agent harnesses for long-horizon tool use… [complete abstract, ~200 words]

## Links

| # | link | handler | status |
|---|------|---------|--------|
| 1 | [Meta AI agent harnesses — LinkedIn](https://…) | linkedin | ok |
| 2 | [Agent Harnesses for Long-Horizon Tool Use](https://arxiv.org/abs/2607.12345) | research | ok |
```

Design notes on each part:

- **Frontmatter is the machine contract** and is authoritative. `/route` should
  be able to make `vet` and `dup` calls from frontmatter alone in the common case.
- **`intent:` is the highest-value field.** Verbatim, never rewritten, never
  summarized. When absent, it is absent — do not invent one.
- **`## Captured` quotes your own words verbatim.** This is what you actually
  wrote, in whatever language you wrote it. It is never touched. When the item
  *is* just a note (case 1), this is the whole file.
- **`## Sources` prints the word count.** This is the single most important
  affordance in the file: it is what lets the agent decide between "the abstract
  is enough" and "open the 11,900-word body". Without it, the choice is blind.
- **`## Lead`** is the abstract for papers, and the first ~120 words of
  `content.md` for everything else. Truncated at a paragraph boundary, marked
  with `…`. Only for the highest-priority source; secondary sources get their
  title and word count only.
- **`## Links`** is the full resolved table. This is the section that grows to 43
  rows for a link list and shrinks to nothing for a voice note.

**Failure renders as information, not as absence:**

```markdown
---
extraction: partial
---

## Sources

1. `extracted/01-medium-1ec4b5bcec35/` — partial (`medium-member-preview`) ·
   `content.md` 264 words — title and opening only, body behind the member wall
```

### 4.3 The generated `triage.md`

`triage.md` is `index.md` files concatenated, oldest first, item headings demoted
one level. Because it is pure concatenation there is no drift and no second
source of truth — this is what `sync.py:render_inbox()` already does, and it
should keep doing it.

Header (written once, ~70 tokens, and it earns them):

```markdown
# Inbox — 14 items, oldest 3 days

<!-- Generated by sync.sh from the item directories. Do not edit.

Each `## <id>` section is one item and is self-contained. Frontmatter is
authoritative: the URL was resolved and the content fetched at capture time, so
liveness and reachability do not need re-checking. Open
`<id>/extracted/NN-*/content.md` only when a lens needs the body — the word
count in Sources tells you the cost. `capture/` and `*_raw.*` are provenance;
do not open them. To file an item, delete `<id>/`. -->
```

The two counts in the title are the operational numbers from
`career-system/info-triage.md` §1.3 (waiting, oldest) and cost nothing to render.

### 4.4 Markdown or Org — decided, with the Emacs plan

**Recommendation: Markdown for the contract and all content; a thin generated
`triage.org` as the Emacs navigation layer.**

The argument that decides it is not aesthetics, it is escaping:

> **Arbitrary extracted web text is not safe to embed in an Org file.**
> A leading `*` becomes a heading. `_foo_` becomes underline and `group_by.agg`
> renders as a subscript — a problem your own `AGENTS.md` documents and works
> around with `=verbatim=`. Markdown tables, code fences and inline `[]()` links
> from `content.md` all need conversion. Every extracted lead would have to pass
> through an escaper, and every escaper bug becomes a corrupted item.

Markdown has no such problem: the extractors emit Markdown, `content.md` is
Markdown, and a lead quoted from it is already valid.

Secondary arguments, all pointing the same way:

- YAML frontmatter is the idiom a code-trained model handles most reliably; an
  Org `:PROPERTIES:` drawer costs two extra lines per item for the same fields.
- One syntax across index and bodies means no conversion step, so no conversion
  failure mode.
- `triage.md` already exists and `render_inbox()` already generates it.

**But Org is right for the review UI**, so generate it too — as *navigation*,
not as content:

```org
#+TITLE: Info triage inbox
#+SUBTITLE: 14 items, oldest 3 days — generated by sync.sh, do not edit
#+STARTUP: overview
# Reading this as an agent? Stop — read triage.md in this directory instead.
# This is a navigation view for a person and holds strictly less than triage.md.

* 4 · 2026-08-11 · paper · Agent Harnesses for Long-Horizon Tool Use
  /new research from Meta on agent harnesses/
  [[file:2026-08-11_100/index.md][index]] · [[file:2026-08-11_100/][directory]] · [[https://arxiv.org/abs/2607.12345][source]]

* 5 · 2026-08-13 · linklist · 43 system design concepts  :partial:
  …
```

Two or three lines per item, folds cleanly, and you can move through it while
Claude Code runs in the window beside it. Being generated, it carries no state —
which is correct, because **deleting the directory is the processed signal** and
marking a generated file would lose that mark on the next sync.

Four details are load-bearing, and each was learned rather than designed:

- **The leading number is the item number**, positional and regenerated on every
  sync, so it always agrees with `triage.md`'s `## N — <id>`. That agreement is
  the whole interface: the reader picks numbers out of the Org view and quotes
  them into a routing request that reads the Markdown one. `sync.sh --regenerate`
  exists so that removing an item locally renumbers both together.
- **No `:PROPERTIES:` drawer.** It restated in eight lines what the heading
  already said, and that is what stopped two dozen items fitting on a screen.
  Everything it held is now in the heading or one link away. In particular the
  item's directory is read back out of `[[file:<id>/][directory]]`, which is the
  only place the id still appears.
- **Not `:ID:`**, then or now: `org-id` owns that property, and writing one per
  item would register them in `.org-id-locations` and dangle on every filing.
- **The label is `title or headline`.** Most items have no title — an Instagram
  post has no `<title>` worth the name — and a bare date and kind name nothing,
  so `index.md` carries a derived `headline:` whenever `title:` is absent (§4.2).

One caveat worth stating plainly: this is a second generated artifact. It is
cheap (~30 lines in `sync.py`) and cannot drift, since both files derive from the
same `index.md` set — but if you would rather not maintain it, ship `triage.md`
alone first and add the Org view once you have felt the review workflow. It is
strictly additive.

**What I would not do:** make `index.md` itself an Org file. It buys Emacs
navigation you can get from the thin view anyway, and pays for it with an
escaping problem on every single item.

### 4.5 Segments, and detecting intent

**Remove segments from the index.** They are a Telegram transport detail — they
exist because sharing a link and typing a comment produces two messages. `/route`
does not care how many Telegram messages an item arrived in; it cares which words
are yours. Keep the numbered segments in `capture/message.md`, where they are
correct and useful for reconstruction, and let the index carry `intent:` plus
`## Captured`. The heading-level inconsistency (§1.3.2) then has nowhere to live.

Intent detection needs no LLM. Three heuristics, each supported by real items:

| Rule | Evidence |
|---|---|
| Text after the last URL **in the same message**, if short, is the note | `2026-08-09_25`: `…/reel/DbW0FoHI1OO/ Try loops` · `2026-08-09_27`: `youtu.be/QOrlzrnfJfs. how to focus macro?` |
| A segment that is **not forwarded**, has **no URL**, is **short**, and sits next to a forwarded or URL-bearing segment, is the note | `2026-08-09_21`: reel + *"Interesting thought to ponder upon. Write and reflect about it"* · `2026-08-09_81`: *"I mean, I'm very curious to try this idea"* after a GitHub link |
| A **forwarded** segment is never the note | `forward_origin` is present and authoritative — `2026-08-13_116`, `2026-08-11_97` |

When the heuristics disagree or nothing matches, **leave `intent:` empty and put
everything in `## Captured`**. An empty field is honest; a wrongly-attributed one
is worse than none, because `/route` will trust it.

Worth one line here even though it is out of scope: the largest single
improvement to routing quality is not in this document. It is the bot asking
*"add a note?"* when an item arrives without one. Your `keep_export.org` — 157
bare URLs with no commentary — is what the absence of that prompt looks like
after two years.

### 4.6 The token budget

Per-item index cost, estimated at ~4 chars/token from the real artifacts in §1.2:

| Item type | Frontmatter | Captured | Lead | Sources+Links | **Total** |
|---|---|---|---|---|---|
| voice note / plain text | 40 | 40 | — | — | **~80** |
| single web page | 90 | 20 | 160 | 40 | **~310** |
| research paper | 110 | 25 | 420 | 60 | **~615** |
| YouTube (long) | 100 | 25 | 160 | 50 | **~335** |
| Instagram reel | 90 | 25 | 140 | 30 | **~285** |
| LinkedIn → paper | 110 | 25 | 420 | 90 | **~645** |
| 43-link list | 90 | 30 | — | 620 | **~740** |

A realistic 20-item day — say 6 notes, 5 web pages, 3 papers, 3 videos, 2 social,
1 link list — is **≈ 6,100 tokens** for the complete digest.

For comparison:

| Approach | Cost for 20 items |
|---|---|
| Today (bare URLs + live retrieval) | ~400 tokens of text, then 20–40 fetch/search round-trips at 1.5–3k each ⇒ **30–60k**, ~10% of them returning nothing |
| Full bodies inline | **100k–300k**, infeasible |
| **This design** | **~6k**, plus selective drill-downs you choose to pay for |

The drill-downs are the point: if three items genuinely need their bodies, that is
3 × ~5k = 15k tokens *spent deliberately*, on content already on disk, with no
403s.

---

## 5. Part C — Seven worked examples, on real items

One per case in your enumeration, using material that exists today.

### Case 1 — Voice note / quick typed idea

Source: `2026-08-12_111` — Russian voice note, already transcribed.
Extractors run: none. No links, nothing to fetch.

```markdown
---
id: 2026-08-12_111
captured_at: 2026-08-12T20:54:53Z
origin: voice
intent: null
kind: note
extraction: none
---

## Captured

> Интересно, как он справится с распознаванием разных языков. Например, сейчас
> я говорю по-русски и сможет ли он распознать русский язык, интересно
>
> — transcribed from a Telegram voice message
```

~90 tokens. The transcription provenance line stays, because `/route` should
discount a garbled phrase as an ASR artifact rather than as your meaning. Note
that the note stays in Russian — `AGENTS.md` requires *your* prose to be English,
which governs what gets written into the org files, not what you dictated.

### Case 2 — Instagram reel with commentary

Source: `2026-08-09_21` — reel URL + *"Interesting thought to ponder upon. Write
and reflect about it"*.
Extractors: `instagram` (caption, image/frame OCR, audio transcription).
Recursion: none. Comments: **excluded** (38% of payload, measured noise).

```markdown
---
id: 2026-08-09_21
captured_at: 2026-08-09T01:01:41Z
origin: instagram
via: "@<owner>"
intent: "Interesting thought to ponder upon. Write and reflect about it"
kind: video
headline: "<caption first line>"
published: 2026-08-07
canonical_url: https://www.instagram.com/reel/DYjm3g7Onqg/
extraction: ok
sources: 1
---

## Captured

> Interesting thought to ponder upon. Write and reflect about it

## Sources

1. `extracted/01-instagram-DYjm3g7Onqg/` — ok · `content.md` 180 words
   (caption + on-screen text + spoken audio)

## Lead

> [first ~120 words of caption + OCR + transcript, in that order]
```

The `intent:` here is doing real work: *"write and reflect"* points at
`Mind/Psyche.org` or a vault note, which the reel's content alone would not.

### Case 3 — YouTube

Source: `2026-08-09_27` — `youtu.be/QOrlzrnfJfs. how to focus macro?`
(intent detected as trailing text after the URL).
Extractors: `youtube`. Recursion: description links, capped at 3.

The size question is settled by the measurements: `ztdTed5egrM`
("Training Agents 3: Reinforcement Learning", 77 min) has a 64 KB transcript —
~16k tokens. It must not be in the index. So:

```markdown
---
kind: video
title: "How to Focus in Macro Photography"
via: "<channel> · 1.3M subscribers"
published: 2026-08-06
duration: 7m22s
canonical_url: https://www.youtube.com/watch?v=QOrlzrnfJfs
extraction: ok
---

## Captured

> how to focus macro?

## Sources

1. `extracted/01-youtube-QOrlzrnfJfs/` — ok · `content.md` 1,240 words ·
   `transcript.txt` 1,180 words · description 40 words

## Lead

> [description, then the first ~120 words of transcript]
```

For a Short, the whole thing is under 500 words and the lead *is* the content —
no drill-down will ever be needed. For the 77-minute lecture, the lead is the
description plus the transcript opening, and the word count tells `/route` that
the body costs 16k tokens. That is exactly the decision you want it making
consciously.

Subscriber count is worth keeping as a one-token `via:` suffix: it is a genuine
`vet` source-quality signal, unlike the view/like counts.

### Case 4 — Medium

Source: `medium_output/1ec4b5bcec35` — *"Qwen3-6-35B runs on a 16 GB M4 Mac mini"*,
status `preview`, 264 words retrieved of a member-only article.

```markdown
---
kind: article
title: "Qwen3-6-35B Runs on a 16 GB M4 Mac Mini — Fully in Memory, No Tricks"
authors: [<author>]
published: 2026-08-…
venue: "Medium · MacOClock"
canonical_url: https://medium.com/macoclock/…-1ec4b5bcec35
extraction: partial
reason: medium-member-preview
---

## Sources

1. `extracted/01-medium-1ec4b5bcec35/` — partial (`medium-member-preview`) ·
   `content.md` 264 words — title and opening paragraphs only

## Lead

> [the 264 words that were retrieved] …
```

This is the case where `partial` earns its existence. 264 words is enough to
identify the article, judge its topic and route it; it is *not* enough to judge
whether the claim holds up. `/route` must be able to see that difference — per
`lenses.md`, *"say so in the finding and mark that judgement unverified"*. It now
can, from frontmatter, without a fetch.

Of the five Medium articles in `medium_output/`, two are `complete` (~1,500 words
each) and three are `preview` (~220–264 words). Expect that ratio.

### Case 5 — LinkedIn (the recursion case)

Source: `linkedin_output/7487448227336716288` — Maxime Labonne on "BabelTele".
Extractors: `linkedin`, then **nested `research`** on the paper URL found in the
author's own first comment.

```markdown
---
id: 2026-08-11_100
origin: linkedin
via: "Maxime Labonne (LinkedIn)"
intent: null
kind: paper
title: "BabelTele: <paper title>"
authors: [<paper authors>]
published: 2026-06-…
venue: arXiv
canonical_url: https://arxiv.org/abs/2606.19857
extraction: ok
sources: 2
---

## Captured

> [no note — shared without commentary]

## Sources

1. `extracted/01-linkedin-7487448227336716288/` — LinkedIn post by Maxime
   Labonne, 2026-07-27 — ok · `content.md` 280 words
2. `extracted/02-arxiv-2606.19857/` — ok · `content.md` 9,400 words ·
   **via 01 (author comment)**

## Lead

> [the paper abstract, ~200 words]

## Links

| # | link | handler | status |
|---|------|---------|--------|
| 1 | [LLMs don't need readable text — Maxime Labonne](https://linkedin.com/…) | linkedin | ok |
| 2 | [BabelTele — arXiv 2606.19857](https://arxiv.org/abs/2606.19857) | research | ok |
```

Four things to notice, because this example justifies several decisions at once:

- **`kind:` is `paper`, not `linkedin`.** The item is promoted to what it turned
  out to be. That is what `dup` and `destination` need; the LinkedIn wrapper is
  transport.
- **The `Lead` is the paper's abstract, not the post's prose.** The post is
  marketing around the artifact; the abstract is the artifact. `vet` judges the
  paper.
- **Nine other comments were dropped** from the index (kept in `comments.md`).
  Several are visibly LLM-generated engagement filler; none change any routing
  decision.
- **`intent: null` is visible.** You shared this without a note, and `/route` can
  see that it is working from the material alone.

### Case 6 — Plain URL: web page, PDF, or paper

Source: `2026-08-13_114` — *"Strong Model Collapse — Dohmatob et al.:
https://arxiv.org/abs/2410.04840"*.
Extractors: `research` (arXiv provider). Recursion: none.

```markdown
---
id: 2026-08-13_114
captured_at: 2026-08-13T00:47:03Z
origin: telegram
kind: paper
title: "Strong Model Collapse"
authors: [Elvis Dohmatob, Yunzhen Feng, Arjun Subramonian, Julia Kempe]
published: 2024-10-07
venue: "arXiv · cs.LG, stat.ML"
doi: 10.48550/arXiv.2410.04840
canonical_url: https://arxiv.org/abs/2410.04840
extraction: ok
sources: 1
---

## Sources

1. `extracted/01-arxiv-2410.04840/` — ok · `content.md` 11,931 words

## Lead

> Within the scaling laws paradigm, which underpins the training of large neural
> networks like ChatGPT and Llama, we consider a supervised regression setting
> and establish the existence of a strong form of the model collapse phenomenon…
> [complete abstract, 1,670 chars]
```

This one is close to ideal, and it shows why research providers get top priority
in §3.3: **every field came from `metadata.json`** — no body parse, no guessing.
`published: 2024-10-07` is the `vet` currency input, delivered free; the subjects
give the destination (`ML/Deep_Learning.org` vs `ML/Generative_AI.org`); the
abstract gives `task` its distinctiveness. The 11,931-word body sits one `Read`
away, priced.

For an HTML page the same shape holds with `kind: article` and a 120-word lead
(`amitness-com-simclr`: 1,458 words, ~2.6k tokens — cheap enough that drilling in
is often worth it, which the word count makes visible). For a PDF it holds with
`kind: pdf` and `page_count`.

### Case 7 — Forwarded text with many links

Two sub-cases, and they render differently on purpose.

**7a — a few links (the common case).** Source: `2026-08-13_116` — forwarded
`@ai_machinelearning_big_data` post about Muse Glimmer, with a Hugging Face GGUF
link and an unsloth guide link, plus a forwarded photo.

```markdown
---
id: 2026-08-13_116
captured_at: 2026-08-13T00:49:44Z
origin: telegram
via: "@ai_machinelearning_big_data (forwarded channel)"
intent: null
kind: release
title: "Muse Glimmer — 30B open-weights model for local agent workflows"
published: 2026-08-12
extraction: ok
sources: 2
---

## Captured

> [forwarded — no note of your own]

## Sources

1. `extracted/01-huggingface-unsloth-Muse-Glimmer-30B-GGUF/` — ok ·
   `content.md` 420 words (model card)
2. `extracted/02-document-unsloth-ai-models-muse-glimmer/` — ok ·
   `content.md` 1,100 words

## Lead

> Muse Glimmer is a 30B open-weights model aimed at continuously-running agent
> systems on local hardware: Apache 2.0, ~24 GB VRAM, focused on agent workflows
> rather than single-turn responses… [~120 words of the forwarded post]

## Links

| # | link | handler | status |
|---|------|---------|--------|
| 1 | [unsloth/Muse-Glimmer-30B-GGUF · Hugging Face](https://huggingface.co/…) | document | ok |
| 2 | [Muse Glimmer — unsloth docs](https://unsloth.ai/docs/models/muse-glimmer) | document | ok |
```

`via:` carries the aggregator channel, which is a real `vet` input: this is a
secondary source relaying a release, so the model card is the primary artifact
and it has been fetched. The forwarded photo becomes one line in Sources
(`03-attachment-photo` with OCR text) rather than a bare `## Segment 2 —
forwarded photo` heading with nothing under it, as it renders today.

**7b — a link list (the ≥8 rule).** Source: `2026-08-13_113` — 43 system-design
concepts, originally as `lnkd.in` shorteners.

```markdown
---
id: 2026-08-13_113
captured_at: 2026-08-13T00:46:15Z
origin: telegram
intent: null
kind: linklist
title: "43 system design concepts — curated resource list"
extraction: ok
sources: 0
link_count: 43
---

## Captured

> I invested almost 3 hours picking the best resources for 43 system design
> concepts. Read all of them to build strong basics.

## Links

| # | topic | link | status |
|---|-------|------|--------|
| 1 | Latency vs Throughput | [Throughput vs Latency — AWS](https://aws.amazon.com/compare/…) | resolved |
| 2 | CAP Theorem | [The CAP Theorem — BMC](https://www.bmc.com/blogs/cap-theorem/) | resolved |
| 3 | ACID Transactions | [ACID Transactions — Redis](https://redis.io/glossary/acid-transactions/) | resolved |
| 4 | Consistent Hashing | [Consistent Hashing — Arpit Bhayani](https://arpitbhayani.me/blogs/…) | resolved |
| … | | | |
```

**Nothing was extracted, and that is correct.** `AGENTS.md` already prescribes
the routing outcome for a link pile: one task plus one Obsidian note holding the
references. The individual article bodies cannot change that decision. 43 resolved
titles cost ~620 tokens; 43 extracted bodies would cost ~107,000 and be discarded.
The `topic` column comes free from the label preceding each URL in the source text.

The same shape covers a pasted email, an RSS excerpt, or the Afisha.London
newsletter (`2026-08-09_77`) — which today occupies 47 lines of `triage.md` and
would become a title, `kind: newsletter`, and its two resolved `text_link`
targets that are currently lost entirely.

---

## 6. Part D — what `/route` needs

Deliberately small. The skill's pipeline, lenses and findings contract are
unchanged; only the scope clause and one `vet` note move.

**1. `_shared/lenses.md`, "Resolving scope"** — add one bullet:

```markdown
- A slice of `~/info-triage-inbox/triage.md` — the generated capture digest. See
  the contract note at the top of that file.
```

**2. `route/SKILL.md`** — one new section:

```markdown
## The info-triage inbox

When the scope is `~/info-triage-inbox/triage.md`, read that file and nothing
else. Each `## <id>` section is one item and is self-contained.

- **Frontmatter is authoritative and already verified.** The URL was resolved and
  the content fetched on the server at capture time. Do not re-fetch to check
  whether a link is alive, and do not search for a title, author or date that is
  already there.
- **`extraction:` says how far that verification got.** `ok` — the body was
  retrieved. `partial` — only part was (the reason key says which); treat
  quality judgements as unverified per `lenses.md`. `failed` — nothing was
  retrieved; say so in the finding and prefer `ASK` over guessing.
- **Bodies are one Read away.** Open `<id>/extracted/NN-*/content.md` only when a
  lens actually needs the body; the word count in Sources is its cost. Never
  open `capture/`, `attachments/`, or any `*_raw.*` or `*-source.*` file.
- **`intent:` is the user's own words.** It carries more routing signal than the
  content does. An empty `intent:` means he shared it without commentary — not
  that it has no purpose.
- **Filing an item means deleting its directory `<id>/`.** `triage.md` is
  generated from those directories and must never be edited; the next sync
  regenerates it and propagates the deletion to the NAS.
```

**3. `lenses.md`, `vet` → *Source quality*** — one sentence appended:

```markdown
When the item comes from the info-triage inbox, "cannot be fetched" is already
answered by `extraction:`; a currency check may still need a search, but start it
from the title, author and date in the frontmatter rather than rediscovering them.
```

That is the whole diff. Everything else — `vet` → `dup` → `task` → destination,
the findings contract, the approval gate, the ceiling of ~15 findings — is
untouched, which is what you asked for.

One consequence worth flagging: with 20 items a day, the **~15-finding ceiling
will bind**. The digest makes each item cheaper but does not make more of them
fit in one approval conversation. Expect to route in slices of ~10, and consider
having `sync.sh` group `triage.md` by day so a slice is a natural unit.

---

## 7. Deliberately not in this design

- **`near:` (precomputed duplicate candidates).** The embedding index over your
  ~3,400 org headings is the highest-value remaining enrichment — `dup` is the
  expensive lens and it is currently a blind repo-wide search per item. But it is
  a separate build, and the contract only needs one extra frontmatter key when it
  lands. Reserve the name now.
- **`suggested_area`.** Same reasoning; add as a distrusted machine field once
  `near:` exists, never as a field you set at capture time.
- **Expiry / archive.** Out of scope here, still the thing most likely to kill
  the system (`career-system/info-triage.md` §1.3).
- **LLM grouping of link lists.** Revisit only if resolved titles turn out to be
  insufficient in practice.
- **Route bots (job / clip / lang).** They change `route:` in frontmatter and
  nothing else in this contract.

---

## Appendix A — `index.md` field reference

| Field | Required | Source | Consumed by |
|---|---|---|---|
| `id` | yes | directory name | all |
| `captured_at` | yes | `metadata.json` | ordering, expiry |
| `origin` | yes | detected: `text｜voice｜telegram｜linkedin｜instagram｜youtube｜medium｜web` | context |
| `via` | when known | `forward_origin`, post author, channel | `vet` source quality |
| `intent` | when detected | §4.5 heuristics | everything |
| `kind` | yes | promoted from the top-priority extraction: `note｜article｜paper｜pdf｜video｜post｜repo｜release｜linklist｜newsletter` | `task`, destination |
| `title` | when known | extraction metadata | `dup`, destination |
| `authors` | when known | extraction metadata | `vet` |
| `published` | when known | extraction metadata | **`vet` currency — the single most valuable enriched field** |
| `venue` | when known | provider / site / channel | `vet` |
| `doi` | papers | research metadata | `dup` |
| `canonical_url` | when any link | link table | `dup` |
| `extraction` | yes | `ok｜partial｜failed｜none` | `vet`, drill-down decisions |
| `reason` | when not `ok` | existing stable reason keys | `vet` |
| `sources` | yes | count of `extracted/` dirs | budgeting |
| `link_count` | when > 5 | link table | link-list detection |
| `near` | *(reserved)* | future embedding index | `dup` |
| `route` | *(reserved)* | future route bots | pipeline selection |

## Appendix B — per-handler matrix

| Handler | Extracts | In the index | Harvests links from | Excluded from index |
|---|---|---|---|---|
| research | metadata, abstract, body md | title, authors, date, venue, subjects, **full abstract** | — | body, source HTML |
| document (html) | metadata, body md | title, author, date, 120-word lead | — | body, source HTML |
| document (pdf) | metadata, body md, page count | title, page count, 120-word lead | — | body, source PDF |
| medium | metadata, article md, availability | title, author, date, lead, **`partial` on preview** | — | body, response HTML |
| linkedin | post text, comments, media, metadata | author, date, 120-word lead | body + **author's own comments** | non-author comments, media |
| youtube | metadata, description, transcript, comments, frame OCR | title, channel + subscribers, date, duration, description + transcript lead | description links, max 3 | transcript body, comments, media |
| instagram | caption, image/frame OCR, audio transcript, comments | owner, date, caption + OCR + transcript lead | — | **all comments**, media, per-frame OCR JSON |
| voice (capture) | transcript | the transcript, marked as dictated | — | the `.ogg` |
| attachment (capture) | image OCR | OCR text if non-trivial | — | the image |

# Related notes — surfacing the plans and vault an item already touches

Status: prototype measured, design proposed, nothing implemented in the daemon.

**Headline results up front.**

1. **How the section is worded matters more than what is in it.** Labelled
   `## Related`, the field cost 1.3% *more* tokens and anchored the agent into
   `MERGE` on all six items. Reworded as unverified candidates, the same rows cut
   tokens **15.5%** and restored a mixed, correct verdict set. §6.
2. **The embedding is not carrying this.** BM25 over the item's full text puts the
   gold answer in the rerank pool 15/15 times; the embedding manages 12/15 and
   ranks two gold answers 448th and 537th. Start without a vector index. §3a.
3. **Best configuration measured: Opus + hedged wording + destination hint — 40%
   fewer tokens than the control**, at 1.6x Sonnet's cost, with visibly better
   routing. Opus reads *less*, not more. §6.
4. **~68% of the token bill is fixed preamble re-read every turn**, not search. The
   lever is turns and batch size, not corpus size. §6a.

## 1. The problem this addresses

`/route`'s `dup` lens is the expensive one, and its cost is structural rather than
incidental. For each inbox item the agent must **invent search vocabulary**, run it
across ~36 Org plan files and ~1,300 vault notes, narrow, then read wide ranges of
the files it hit to see whether the match is real. It does this because it starts
from an item and has no idea where in the repo that item's neighbourhood lives.

Precomputing that neighbourhood is server-side work. Nothing about it needs an
agent loop: it is retrieval, and retrieval is exactly what an embedding index plus a
lexical index do better than repeated guessed greps. Whether "better" also means
"cheaper in tokens" was the question this evaluation set out to answer, and §6
answers it: no.

Observed directly (see §6): on six real items the unaided run issued 17 repo-wide
greps with patterns like `watermark`, `KV cache|kv-cache|prefill`,
`semantic search|dual.encoder`, `distill|model stealing|reasoning trace`, then
re-ran each narrowed to a file, then read `ML/Ranking.org` at three separate
offsets. The run given a `## Related` field issued **no exploratory greps at all** —
it went straight to `sed -n '290,320p' ML/Generative_AI.org` and the equivalent for
each cited line, verifying pointers instead of discovering them.

## 2. What is being retrieved against

Two corpora, both already on the laptop and both in git:

| corpus | unit | count |
|---|---|---|
| Org plans (`notes/org`) | heading + body, with ancestor breadcrumb | 2,902 tasks + 571 sections + 32 charters |
| Obsidian vault (`notes/obsidian`) | `##`/`###` section, windowed at 280 words | 4,472 |

**7,977 units, 561k words total.** Small. Exact cosine over the whole set is
microseconds; no ANN index is needed or wanted.

Org structure is worth exploiting and cheap to parse: a heading carrying a TODO
keyword is a task, its ancestors give a breadcrumb (`ML/Ranking.org > Recommender
systems > Sequential and generative recommendation`), and that breadcrumb is
prepended to the embedded text. Vault notes get `folder/note > section` the same way.
Drawer lines, `SCHEDULED:` and `:PROPERTIES:` are stripped before embedding.

Charters (a file's `#+SUBTITLE:` plus opening prose) are indexed but held **out** of
the duplicate list: a charter is never a duplicate of anything. It is a
*destination* signal, which is a different output.

## 3. Retrieval design

Three stages. The third is what makes the result trustworthy.

1. **Recall — hybrid, cheap.** BM25 over tokenized units, plus dense cosine from
   `intfloat/multilingual-e5-small` (118M, 384-dim). Fused with reciprocal rank
   fusion, top 40 kept. The query is the item's `title`/`headline` + `intent` + the
   first 120 words of its Lead. Multilingual matters: the vault and plans contain
   Russian and Spanish, and a monolingual encoder mismatches them.
2. **Rerank — cross-encoder.** `BAAI/bge-reranker-base` (278M, XLM-R, multilingual)
   scores the 40 candidates against a **short** query — the title plus the first
   sentence, ~28 words.
3. **Gate — absolute score.** Emit at most 2 plan rows and 2 vault rows, each above
   a logit cut, labelled `strong` (logit ≥ 0) or `likely` (≥ −3).

Three findings drove that shape, each of which cost a wrong first attempt:

- **A bi-encoder alone cannot abstain.** e5 cosines for this corpus sit between
  0.76 and 0.93 whether the item belongs here or not. A control item about restoring
  a Vespa scooter engine scored 0.83 against `Work/Prep.org` — indistinguishable from
  a real match. No cosine threshold separates them.
- **The rerank query must be short.** Given the full 120-word lead, the
  cross-encoder saturated at probability 1.00 for *every* candidate on 5 of 12 real
  items — a multi-topic query makes everything look relevant, destroying exactly the
  ordering the rerank exists to produce. Truncating to title + first sentence fixed
  it.
- **Use the raw logit, not the sigmoid.** The probability saturates at both ends;
  the logit still moves when the model is merely unsure, which is what the abstain
  gate needs.

## 3a. Does the embedding earn its place? Mostly not

Ablation against 15 hand-labelled gold units, each retrieval mode scored alone:

| mode | hit@1 | hit@5 | hit@40 (the rerank pool) |
|---|---|---|---|
| BM25 over the item's full text | 4/15 | 8/15 | **15/15** |
| BM25 over title only | 4/15 | 8/15 | 14/15 |
| dense (`multilingual-e5-small`) | 2/15 | 8/15 | 12/15 |

**BM25 alone gets every gold answer into the top 40, which is all the reranker
needs.** The embedding misses three outright and ranks two of them 448th and 537th
— the Walmart semantic-search item and the Pinterest recsys item, both cases where
the vocabulary is shared but the phrasing is not. Dense wins where vocabulary
genuinely diverges (the Raschka repo item: rank 4 against BM25's 20; the HSK/Anki
item: 2 against 13), so hybrid retrieval still has the best recall — but the margin
does not justify the infrastructure on day one.

**Recommendation: ship BM25 → top 40 → cross-encoder, with no vector index.** That
removes the embedding model, the 12 MB index transfer and the rebuild job entirely;
peak memory falls from 1.47 GB to ~600 MB and the index build from 3.2 minutes to
seconds of pure Python. Add embeddings later if measurement shows misses.

One distinction this does *not* license: "grep is good enough." The agent's greps are
one to three guessed terms. This is IDF-weighted BM25 over the item's whole title and
lead, which is a different instrument and still server-side work.

## 4. Measured quality

22 queries: the 12 real inbox items (the three "Test connection" captures excluded),
8 synthetic items spanning photography, piano, Chinese, nutrition, tango, ML system
design interviews, statistics and Rust, and 2 deliberate out-of-scope controls
(Vespa restoration, beekeeping).

**Precision@1 on the plan side: 15/20 in-scope items exactly right, 3 more landing
in the right file on a neighbouring task, 2 wrong.** Judged by hand.

Exact hits worth naming, because they are the cases `dup` exists for:

- KV-cache paper → `ML/Generative_AI.org:48` *KV cache as an architectural
  constraint* **and** `ML/Systems.org:324` *Prefill-decode disaggregation and KV
  cache transfer between instances*. Both correct, in different files — the
  cross-file case that costs a repo-wide search.
- Claude watermarking post → `ML/Generative_AI.org:735` *Survey how
  machine-generated text is detected: watermarking, zero-shot statistics,
  classifiers* and vault `ML & AI/Concepts/Detecting machine-generated text.md:6`.
- "Meta released a new open-weight LLM" → `ML/Generative_AI.org:56` *Map the
  open-weight landscape: Llama, Qwen, DeepSeek, Mistral*.
- LLMs-from-scratch repo → `ML/Generative_AI.org:1068` *Read Build a Large Language
  Model (From Scratch) (Raschka)* — the same book.
- A/B peeking (synthetic) → `ML/Statistics.org:46`, `ML/Ranking.org:711`,
  `ML/Foundations.org:387` and `Statistics/Peeking and Sequential Testing.md`. All
  four correct, across four files.

**Precision falls off a cliff after rank 1–2** — roughly 40% at k=3. That is why the
field emits two rows per side, not five. Rank is reliable; depth is not.

**Abstention works, with one honest caveat.** Both controls score far below the cut
(−4.5 and −8.5) and emit nothing. But the correct match for the retail semantic-search
item scored −5.16, below the control's −4.48: a single global threshold cannot
separate a weak-but-correct hit from a confident-but-irrelevant one. At the proposed
cut of −3, **16 of 20 in-scope items get a pointer, both controls abstain, and about
88% of emitted rows are correct.** Missing a pointer costs nothing; a wrong one costs
trust, so the cut is deliberately on the conservative side.

The 2 outright errors are instructive: a paper titled *GitSkills: A Dataset of Agent
Skills on GitHub* matched `Work/Programming.org:593` *git bisect* — lexical bleed on
"git" that the reranker did not veto.

## 5. Cost on the NAS

Measured single-threaded on CPU (M1 core, one thread, no GPU):

| operation | measured | Synology estimate (3–5× derate) |
|---|---|---|
| index build, all 7,977 units | 3.2 min | 10–16 min, one-off / nightly |
| incremental update, changed files only | seconds | seconds |
| rerank 40 candidates for one item | 3.2 s | 10–16 s |
| peak RSS, both models resident | 1.47 GB | same |

Against a stated budget of 15 minutes per item and ~5 GB, this is not close. The
per-item cost is seconds. Nothing here needs approximate nearest neighbours, batching
tricks, or a second core. A Raspberry Pi with 2 GB could run the bi-encoder alone
(~600 MB) though not comfortably both.

Model choice is deliberately small: the reranker, not model size, is what fixed
quality. A larger bi-encoder would not have solved the abstention problem, and
`bge-reranker-v2-m3` (568M) was tried and offered no visible gain over the 278M base
at four times the cost.

## 6. Effect on `/route` — four runs

Same six real items, same skill. One run per condition, so treat single-digit token
differences as noise; the verdict columns are not noise.

| | model | field | tokens | cost | turns |
|---|---|---|---|---|---|
| control | Sonnet | none | 997,415 | $1.0237 | 33 |
| anchored | Sonnet | `## Related` | 1,010,462 (+1.3%) | $1.0103 | 26 |
| hedged | Sonnet | unverified candidates | 843,168 (−15.5%) | $0.9445 | 24 |
| + destination | Sonnet | + likely destination file | **696,545 (−30.2%)** | $0.8625 | 23 |
| + destination | **Opus** | + likely destination file | **600,912 (−39.8%)** | $1.3894 | 24 |

Three separate effects, each measured:

**Wording is worth ~17 points.** Labelled `## Related`, the field cost 1.3% *more*
tokens and returned `MERGE` for all six items. On the two items where the control and
the anchored run found the same neighbouring line, they reached opposite conclusions
about whether that neighbour absorbs the item. Rewording the identical rows as
"machine retrieval, not a finding … an item with a neighbour is as likely to need a
new task beside it as a merge into it" cut tokens 15.5% and restored a mixed verdict
set (4 merge, 2 new). **The caveat is part of the feature, not decoration.**

**The destination hint is worth another ~17 points**, and is free — it is the *file*
of the top-ranked neighbour, not a separate computation. Deriving it instead from the
32 file charters was tried and measurably fails (~7/20: Rust → `Admin/Funds.org`,
photography → `Admin/Data.org`), because a charter is too abstract to match a concrete
paper title. Neighbour-file accuracy is ~90% (top-1 was exactly right 15/20 and in the
right file 18/20).

**Opus is the largest quality step, and costs 1.6x, not 5x.** It used *fewer* tokens
than Sonnet on identical input (601k vs 697k) because it read less: "items 1 and 3
have complete abstracts and needed no Read" — using the `lead:` field exactly as the
skill documents. Net cost ratio against Sonnet on the same variant is **1.61x**.

The quality gap is not marginal. On the Walmart item, Sonnet proposed one new task;
Opus identified that four existing tasks (`:143`, `:145`, `:147`, `:149`) already
cover most of the post and isolated the single genuinely uncovered idea
(legacy-aware distillation warm-start when swapping a production backbone). That is
precisely what `dup` exists to do. On the KV-cache item, Opus **rejected the
destination hint and said why** — "the `Generative_AI.org:48` neighbour is the wrong
file: this is operational, not architectural" — which is the ideal relationship
between agent and field. It also cited the user's `intent:`, proposed the
distinguishing exercise `AGENTS.md` asks for, raised a secondary cross-file note per
`lenses.md`, and refused to invent a missing link.

**Recommendation: Opus, hedged wording, destination hint.** 40% fewer tokens than the
control at 1.36x its cost, for materially better routing.

## 6a. Where the tokens actually go

**Turn one costs 49,000 tokens before a single note is read.** Context then grows to
~98k by the end of a six-item run. Across 45 billed assistant messages that fixed
preamble accounts for roughly **68% of all context read**.

The user-controlled share of that 49k is small:

| | tokens |
|---|---|
| `AGENTS.md` (20 KB) | ~5,100 |
| `_shared/lenses.md` | ~2,500 |
| `route/SKILL.md` | ~1,900 |
| `.claude/generated-context.md` | ~1,900 |
| **total editable** | **~11,400** |
| system prompt + tool definitions | ~37,000 (not editable) |

So trimming the notes' own instruction files caps out at maybe 5–8% of the bill.
**Splitting the Org or vault files would not help at all** — they are read in bounded
chunks, and a line-anchored pointer already prevents the "read `ML/Ranking.org` at
three offsets" pattern the unaided run showed.

Cost is `turns × context`, and context grows ~8k per item. That makes it
**superlinear in batch size**: roughly `T·fixed + growth·T²/2`. Six items doubled the
context, 49k → 98k.

**Practical guidance: smaller batches than intuition suggests.** Splitting does *not*
waste the fixed preamble the way it first appears to: total turns are roughly
constant in the number of items, so `T·fixed` is paid either way, and only the
quadratic term is halved. What splitting does cost is one session start-up (~4 turns
× 49k ≈ 200k tokens).

Fitting the measured constants (`fixed` 49k, ~4.8 turns/item, ~1.7k context growth
per turn, ~4 start-up turns) and minimising over batch size `m` for 12 items:

| items per session | sessions | modelled total |
|---|---|---|
| 12 | 1 | 6.25M |
| 6 | 2 | 5.04M |
| 4 | 3 | 4.79M |
| 3 | 4 | 4.76M |
| 2 | 6 | 4.94M |
| 1 | 12 | 5.97M |

The curve is flat between about 3 and 6 and rises sharply above 8. **One batch of 30
is the worst option available.** The model ignores cross-item reuse — two items
routed to the same file share that file's read — which pushes the optimum up somewhat,
so 4–8 is the defensible range and the exact value is not worth chasing. At the
measured rate this is ~$0.17/item.

**On model choice.** Measured in §6: Opus costs 1.61x Sonnet on this task, not the
5x a naive per-token comparison suggests, because it does fewer redundant reads. For
a task whose whole output is judgement — merge or not, which file, is the source
primary — that is a good trade, and it is the recommended configuration.

## 6b. Does this survive a 10x vault?

The vault is expected to grow by roughly an order of magnitude; the Org plans are
not. Measured by subsampling the vault to 10/25/50/100% with the plans held fixed —
a 10x span — and watching where the gold answer lands.

| vault | corpus | Org-side gold | vault-side gold |
|---|---|---|---|
| 10% | 3,985 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 6/6, hit@40 6/6, median 1 |
| 25% | 4,651 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 6/6, hit@40 6/6, median 1 |
| 50% | 5,760 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 5/6, hit@40 6/6, median 1 |
| 100% | 7,977 | hit@5 9/15, hit@40 15/15, median 3 | hit@5 5/6, hit@40 6/6, median 2 |

**hit@40 does not move at all** across a 10x change, on either side. Since the pool
is what the reranker consumes, the part that determines final quality is unaffected.
Only the top-5 ordering drifts, and only slightly.

Two structural properties make that true, and both are load-bearing:

1. **Score the two corpora as separate buckets.** The design already emits `plan` and
   `vault` rows separately. Pooled into one global top-4, a 10x vault would flood the
   list with vault rows and squeeze the plan rows — the ones that answer `dup` — out
   entirely. This is not cosmetic.
2. **Give each corpus its own BM25 index.** The Org-side drift above (11/15 → 9/15)
   is *entirely* an artifact of sharing IDF and average document length with the
   vault: the Org corpus itself did not change. With one index per corpus the Org
   side becomes exactly invariant — measured at 11/15, hit@40 15/15, median 2 at
   **every** vault size. That is a two-line change and it should be made now.

Compute is flat in corpus size for the part that costs anything: the reranker always
sees 40 candidates. A 45,000-unit BM25 index is still seconds to build and
milliseconds to query. **A 10x vault is a non-event for this design**, provided the
two properties above hold.

The one thing that will need revisiting is the abstain threshold. More vault units
means more near-misses clearing any fixed cut, so the vault side should be expected
to need a stricter threshold than the plan side as it grows — worth re-fitting when
the vault has actually doubled rather than pre-emptively.

## 7. Where this should run — reviewing the GitHub-polling proposal

> **Superseded — see `docs/Related-notes-implementation-plan.md` §1.** This section
> argued for a NAS pipeline step fed by a laptop-pushed index, against the original
> GitHub-polling proposal. §3a then removed the embedding model, which was the only
> component expensive enough to justify precomputing anywhere. Measured end to end,
> the whole job is **63 s on the laptop for 15 items** (0.6 s to parse both corpora,
> 0.4 s to build two BM25 indices, 4.2 s to load the reranker, 57.4 s to rerank).
> It therefore runs entirely in `sync.py`, after the download and before view
> generation. The reasoning below about *not* polling GitHub still stands and is
> now stronger: there is nothing to fetch and nothing to push.

The proposal was: the NAS pulls both repos from GitHub on a timer and rebuilds
embeddings in the background, with a token and repo paths in `config.yaml`.

**The polling half is the part to reconsider.** Everything the index is built from
already exists on the laptop as a live working copy, and the laptop is the machine
that runs `sync.sh` and talks to the NAS. Pulling from GitHub adds a bearer token to
`.env`, a scheduler, a network failure class, and a full mirror of a vault
containing health, finance and psychology notes onto a box whose dashboard is
served unauthenticated to the LAN. It buys nothing the laptop cannot hand over
directly.

**Recommended:** keep the computation on the NAS as a pipeline step, but have the
laptop *push* the index during the sync it already performs. The vectors are 12 MB
and the unit metadata a few MB more; the NAS needs the two models (~900 MB on disk)
to encode one query per item and nothing else. No token, no polling, no mirror.

**Fits the existing contracts as follows:**

- A new step `related-notes`, declared after `content-extraction` and before
  `index-render`, per the ordering rules `config.py` already validates. It runs on
  `info` only — `job`, `clip` and `lang` have something downstream that processes
  them, and this is enrichment.
- It puts its rows on `ProcessingResult`, exactly as `link-discovery` puts the link
  table there, and `index-render` prints a `## Related` section with a `related: N`
  frontmatter count. A list, not a table, per the `## Sources` rule.
- It is enrichment and therefore never fails an item. A missing or stale index is a
  recorded problem and an omitted section — never a withheld capture.
- Placing it in `index.md` keeps `index.md` the only per-item contract and leaves
  `sync.py`'s concatenation of `triage.md` untouched. Computing it laptop-side at
  sync time would be fresher, but only by giving `triage.md` a rendering path of its
  own, which the design forbids for good reason.

**The staleness this leaves is real but small.** An item carries the neighbourhood as
of its capture, and items are triaged the same day or the next. Re-running the step
on `promote_if_current()` already refreshes it on any Telegram edit.

## 8. Other server-side work worth doing

Ordered by value per unit of effort, all measurable against the same corpus.

1. **Intra-inbox duplicates.** Items 1 and 6 in the current inbox are byte-identical
   captures of the same LinkedIn post. The agent currently discovers that by reading
   both. A cosine over the items already being embedded catches it for free, and the
   second item can carry `duplicate_of: <id>`.
2. **A destination hint.** The charter units already rank well against items and are
   currently discarded. `Body/Nutrition.org`, `Play/Tango.org` and `ML/RL.org`
   charters each surfaced as the top hit for their topic. One `destination:` field
   would collapse the skill's fourth step — which currently means reading candidate
   charters — into a check.
3. **Drop the test captures.** Three of fifteen inbox items are
   "Test connection from Info Triage Capture options page". They cost a section in
   `triage.md` each and are pure noise.
4. **Publication-date currency signal.** `vet` is told to run a web search whenever
   title, subfield and date suggest an item may have been overtaken. `published:` is
   already in the frontmatter; the server could mark items older than a threshold in
   a fast-moving area, so the search runs on the few that need it rather than on
   suspicion.
5. **Do not put an LLM in this path.** The extractor contract already forbids it, and
   it holds here: this is retrieval, it costs seconds, and a summarizer would make it
   slow, non-deterministic and unfalsifiable.

## 9. What is not settled

- The threshold is fitted to 22 hand-judged queries on one person's corpus. It should
  be rechecked once real items accumulate beyond the current ML-heavy sample.
- Chunking the vault at 280 words is a guess that has not been ablated.
- Whether two rows per side is the right budget, versus one, is not measured — only
  that five is too many.
- **Every A/B here is n=1 per condition.** The verdict *pattern* (7 `MERGE` versus a
  mixed set) is too stark to be run-to-run noise, but the token percentages are not.
  Repeat each condition three times before quoting −15.5% to anyone.
- The token result is measured at six items, where fixed context dominates. Re-measure
  at 12, which §6a argues is near the practical batch ceiling.
- Recall was never measured properly. `hit@k` against one hand-picked gold unit per
  query is not recall, because no query has an exhaustive relevance set. The reported
  numbers say "did a useful pointer surface", which is the operational question but
  not the same question.
- The growth simulation subsamples downward and reads the trend upward. It shows the
  design is insensitive to distractor *count*; it cannot show what happens if the
  vault grows in a way that changes its *character* — many more notes on topics the
  plans also cover would raise the false-positive rate in a way this cannot predict.

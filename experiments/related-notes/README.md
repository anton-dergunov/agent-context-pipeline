# Experiment · which existing notes does an item belong with?

**Question.** Can the plans and notes an inbox item most likely belongs with be found without an
agent, well enough to trust, and does handing them to the routing agent make routing cheaper?

**Status.** Measured 18 Aug 2026 on one person's corpus. Built the same day as
`info_triage/neighbours.py`: BM25, a cross-encoder rerank, an abstain gate, and a hedged block in
each item's `index.md`. Best configuration measured: 40% fewer tokens than the control. Every A/B
below is one run per condition.

**Serves.** [`docs/architecture/related-notes.md`](../../docs/architecture/related-notes.md).

## Apparatus

Not wired into the daemon and not covered by the test suite. The corpora are private note
repositories, so no result files are committed; the scripts are here so the numbers can be
re-derived and the thresholds re-fitted when the inbox stops being mostly about machine learning.

| Script | Does |
|---|---|
| `corpus.py` | parses the Org plans and the Obsidian vault into `units.jsonl` |
| `embed.py` | embeds the units into `vecs.npy` |
| `search.py` | hybrid recall (BM25, dense, reciprocal rank fusion) and the rerank |
| `rerank.py` | the cross-encoder, scored as a raw logit |
| `items.py` | the test queries: the real inbox items plus synthetic ones |
| `eval3.py`, `eval4.py` | run the queries and write the ranked results |
| `ablate.py` | scores BM25 and the embedding separately against hand-labelled answers |
| `growth.py` | subsamples the vault to simulate growth |
| `destination.py` | the rejected charter-based destination hint |

```bash
cd experiments/related-notes
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install sentence-transformers torch
./.venv/bin/python corpus.py     # notes/org + notes/obsidian -> units.jsonl
./.venv/bin/python embed.py mps  # -> vecs.npy  (use `cpu` off Apple silicon)
./.venv/bin/python eval4.py      # 22 queries -> results4.json
```

The corpus roots are read from `INFO_TRIAGE_ORG_ROOT` and `INFO_TRIAGE_OBSIDIAN_ROOT`.

## Corpus and queries

7,977 units, 561,000 words: 2,902 Org tasks, 571 Org sections, 32 file charters, and 4,472 vault
sections windowed at 280 words. Exact scoring over the whole set takes microseconds; no approximate
index is needed.

22 queries: the 12 real inbox items (three test captures excluded), 8 synthetic items spanning
photography, piano, Chinese, nutrition, tango, system design interviews, statistics and Rust, and 2
deliberate out-of-scope controls (restoring a scooter engine, beekeeping).

## Retrieval shape

The first prototype used hybrid recall: BM25 plus dense cosine from `intfloat/multilingual-e5-small`
(118M, 384 dimensions), fused with reciprocal rank fusion, top 40 kept, then reranked by
`BAAI/bge-reranker-base` (278M). Three findings each cost a wrong first attempt:

- **A bi-encoder alone cannot abstain.** Cosines for this corpus sit between 0.76 and 0.93 whether
  the item belongs or not. The scooter control scored 0.83 against a work-preparation file,
  indistinguishable from a real match.
- **The rerank query must be short.** Given the full 120-word lead, the cross-encoder saturated at
  probability 1.00 for every candidate on 5 of 12 real items. Truncating to the title plus the first
  sentence (about 28 words) fixed it.
- **Use the raw logit.** The sigmoid saturates at both ends; the logit still moves when the model is
  merely unsure, which is what an abstain gate needs.

`bge-reranker-v2-m3` (568M) was tried and showed no visible gain over the 278M base at four times
the cost.

## Does the embedding earn its place?

Mostly not. Each retrieval mode scored alone against 15 hand-labelled answers (`ablate.py`):

| Mode | hit@1 | hit@5 | hit@40, the rerank pool |
|---|---|---|---|
| BM25 over the item's full text | 4/15 | 8/15 | **15/15** |
| BM25 over the title only | 4/15 | 8/15 | 14/15 |
| dense (`multilingual-e5-small`) | 2/15 | 8/15 | 12/15 |

BM25 alone gets every answer into the top 40, which is all the reranker needs. The embedding misses
three outright and ranks two of them 448th and 537th, both cases where the vocabulary is shared but
the phrasing is not. Dense wins where vocabulary really diverges (one item at rank 4 against BM25's
20, another at 2 against 13), so hybrid has the best recall, but the margin did not justify the
infrastructure. **Decision: BM25, top 40, cross-encoder, no vector index.** Peak memory falls from
1.47 GB to about 600 MB and the index build from 3.2 minutes to seconds.

This does not mean a guessed search term is good enough. An agent's searches are one to three
guessed words; this is IDF-weighted BM25 over the item's whole title and lead.

## Quality

**Precision at rank 1 on the plan side: 15 of 20 in-scope items exactly right, 3 more in the right
file on a neighbouring task, 2 wrong.** Judged by hand.

The hits that matter are the cross-file ones, which cost an agent a repository-wide search: a paper
on KV caching matched one task in a generative-AI file and another in a systems file, both correct;
a synthetic item on peeking at A/B tests matched four correct places across four files.

**Precision falls off after rank 1–2**, to roughly 40% at rank 3. That is why two rows are emitted
per side, not five.

**Abstention works, with one caveat.** Both controls score far below the cut (−4.5 and −8.5). But
one correct match scored −5.16, below a control's −4.48: a single global threshold cannot separate a
weak correct hit from a confident irrelevant one. At a cut of −3, 16 of 20 in-scope items get a
pointer, both controls abstain, and about 88% of emitted rows are correct.

The two outright errors are lexical bleed. A paper titled *GitSkills: A Dataset of Agent Skills on
GitHub* matched a task about `git bisect`, and the reranker did not veto it.

## Cost

Single-threaded on one M1 core, no GPU, with both models of the first prototype:

| Operation | Measured |
|---|---|
| index build, all 7,977 units | 3.2 min |
| rerank 40 candidates for one item | 3.2 s |
| peak memory, both models resident | 1.47 GB |

As built, without the embedding, for 15 items on the laptop with a warm model cache:

| Stage | Time |
|---|---|
| parse both corpora | 0.6 s |
| build two BM25 indexes | 0.4 s |
| load the reranker | 4.2 s |
| rerank 15 items × 40 candidates | 57.4 s |
| **total added to a sync** | **62.6 s** |

## Effect on routing

The same six real items and the same routing skill, one run per condition. Treat single-digit token
differences as noise; the verdict columns are not noise.

| Condition | Model | Field | Tokens | Cost | Turns |
|---|---|---|---|---|---|
| control | Sonnet | none | 997,415 | $1.0237 | 33 |
| anchored | Sonnet | `## Related` | 1,010,462 (+1.3%) | $1.0103 | 26 |
| hedged | Sonnet | unverified candidates | 843,168 (−15.5%) | $0.9445 | 24 |
| + destination | Sonnet | + likely destination file | **696,545 (−30.2%)** | $0.8625 | 23 |
| + destination | Opus | + likely destination file | **600,912 (−39.8%)** | $1.3894 | 24 |

Three separate effects:

**Wording is worth about 17 points.** Labelled `## Related`, the field cost more tokens than no
field and returned "merge" for all six items. On the two items where the control and the anchored
run found the same neighbouring line, they reached opposite conclusions about whether it absorbs the
item. Rewording the identical rows as unverified machine retrieval cut tokens 15.5% and restored a
mixed verdict set (4 merge, 2 new).

**The destination hint is worth about another 15 points**, and is free: it is the file of the
top-ranked neighbour. Deriving it from the 32 file charters (`destination.py`) fails, about 7 of 20
right, because a charter is too abstract to match a concrete paper title. Neighbour-file accuracy is
about 90%: the top row was exactly right 15 of 20 times and in the right file 18 of 20.

**The stronger model is the largest quality step and costs 1.6 times, not 5.** It used fewer tokens
on identical input (601k against 697k) because it read less, using the `lead:` field to skip bodies
whose abstract was already complete. On one item the smaller model proposed one new task, while the
larger identified four existing tasks that already cover most of the post and isolated the single
uncovered idea. On another it rejected the destination hint and said why, which is the intended
relationship between agent and field.

## Where the tokens go

Turn one costs 49,000 tokens before a single note is read, and context grows to about 98,000 by the
end of a six-item run. Across 45 billed assistant messages that fixed preamble is roughly 68% of all
context read.

| | Tokens |
|---|---|
| the notes repository's own instruction files | about 11,400 |
| system prompt and tool definitions | about 37,000, not editable |

Trimming the instruction files therefore caps out at 5–8% of the bill, and splitting the plan files
would not help, since they are read in bounded chunks and a line-anchored pointer already prevents
re-reading.

Cost is turns times context, and context grows about 8,000 tokens per item, which makes it
superlinear in batch size. Fitting the measured constants (49k fixed, about 4.8 turns per item,
about 1.7k growth per turn, about 4 start-up turns) for 12 items:

| Items per session | Sessions | Modelled total |
|---|---|---|
| 12 | 1 | 6.25M |
| 6 | 2 | 5.04M |
| 4 | 3 | 4.79M |
| 3 | 4 | 4.76M |
| 2 | 6 | 4.94M |
| 1 | 12 | 5.97M |

The curve is flat between about 3 and 6 and rises sharply above 8. The model ignores reuse between
items filed to the same file, which pushes the optimum up somewhat, so 4 to 8 is the defensible
range. At the measured rate this is about $0.17 per item.

## Does it survive a ten times larger vault?

The vault is expected to grow by an order of magnitude; the plans are not. `growth.py` subsamples
the vault with the plans held fixed and watches where the answer lands.

| Vault | Corpus | Plan-side answers | Vault-side answers |
|---|---|---|---|
| 10% | 3,985 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 6/6, hit@40 6/6, median 1 |
| 25% | 4,651 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 6/6, hit@40 6/6, median 1 |
| 50% | 5,760 | hit@5 11/15, hit@40 15/15, median 2 | hit@5 5/6, hit@40 6/6, median 1 |
| 100% | 7,977 | hit@5 9/15, hit@40 15/15, median 3 | hit@5 5/6, hit@40 6/6, median 2 |

hit@40 does not move at all, on either side. Two properties make that true:

1. **The two corpora are scored as separate buckets.** Pooled into one global top four, a larger
   vault would squeeze the plan rows out entirely.
2. **Each corpus has its own BM25 index.** The plan-side drift above (11/15 to 9/15) is entirely an
   artifact of sharing term statistics with the vault. With one index per corpus the plan side is
   exactly invariant: 11/15, hit@40 15/15, median 2 at every vault size.

The abstain threshold is the one thing that will need revisiting. More vault units means more near
misses clearing any fixed cut.

## What is not settled

- The threshold is fitted to 22 hand-judged queries on one person's corpus.
- Chunking the vault at 280 words has not been ablated.
- Whether two rows per side is right, against one, is not measured. Only that five is too many.
- **Every A/B is one run per condition.** The verdict pattern is too stark to be noise; the token
  percentages are not. Repeat each condition three times before quoting them.
- The token result is at six items, where fixed context dominates.
- Recall was never measured properly: hit@k against one hand-picked answer per query says whether a
  useful pointer surfaced, which is the operational question and not the same one.
- The growth simulation subsamples downward and reads the trend upward. It shows insensitivity to
  the number of distractors, not to a vault whose character changes.

## An earlier prototype

A one-evening prototype in June 2026 triaged about 25 saved items with a small cloud model and an
embedding search over the same corpora. Its negative results are recorded because each would
otherwise be tried again:

- **A cheap model asked whether an item is still useful approves nearly everything.** It chose "both
  a task and a note" for 15 of 20 items in one run and 16 of 25 in another, and dropped only bare
  social links. Given file names but not section names, it invented sections.
- **Bi-encoder similarities bunch together with a second model too.** With `bge-small`, unrelated
  notes scored 0.67 to 0.74.
- **Clustering near-duplicates on a model's own title and topics fails.** A 0.62 centroid threshold
  put 18 of 25 items into one cluster.

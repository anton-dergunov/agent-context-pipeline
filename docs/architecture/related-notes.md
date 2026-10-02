# Related notes

Each `info` item arrives with pointers to the existing plans and notes it most likely belongs with,
so the routing agent starts from candidates instead of a search. The pointers are computed on the
laptop after a sync, by `src/info_triage/neighbours.py`, and appended to the item's `index.md`.

Every number here comes from [`experiments/related-notes/`](../../experiments/related-notes/README.md),
which also holds the scripts that produced them. The parameters are measured, not chosen: re-measure
before changing one.

## The problem

Checking whether an item duplicates something already planned is the expensive part of routing, and
the cost is structural. Starting from an item, the agent has to invent search vocabulary, run it
across a few dozen plan files and over a thousand notes, narrow, and then read wide ranges of the
files it hit to see whether a match is real. On six real items an unaided run issued 17 repository-wide
searches, repeated each narrowed to one file, and read one plan file at three separate offsets. Given
candidates, the same run issued no exploratory searches at all: it went straight to the cited lines
and verified them.

Finding that neighbourhood is retrieval, and retrieval needs no agent loop.

## What is searched

Two corpora, both already on the laptop as live working copies:

| Corpus | Unit | Measured size |
|---|---|---|
| Org plans | a heading with its body, plus its ancestor breadcrumb | 2,902 tasks, 571 sections, 32 file charters |
| Obsidian vault | a `##` or `###` section, windowed at 280 words | 4,472 |

About 8,000 units and 561,000 words. Org structure is cheap to exploit: a heading carrying a TODO
keyword is a task, and its ancestors give a breadcrumb (`ML/Ranking.org > Recommender systems >
Sequential recommendation`) that is prepended to the indexed text. Vault notes get
`folder/note > section` the same way. Drawer lines, `SCHEDULED:` and property blocks are stripped.

A file's charter (its subtitle and opening prose) is indexed but never offered as a neighbour: a
charter is not a duplicate of anything.

Some Org files are left out altogether. By default these are the editor configuration's own
`workspace.org` and `init.org`; the `org_exclude` setting adds unprocessed piles such as an inbox
file. A pile matches everything that has not been filed out of it yet, so a pointer into it says
nothing about where an item belongs. Settings are described in [`sync.md`](sync.md#settings).

## Retrieval

Three stages. The third is what makes the result trustworthy.

1. **Recall: BM25, one index per corpus.** The query is the item's `title` or `headline`, its
   `intent`, and the first 120 words of its lead. The top 20 of each corpus form a pool of 40.
2. **Rerank: a cross-encoder.** `BAAI/bge-reranker-base` (278M parameters, multilingual, because the
   notes contain Russian and Spanish) scores the 40 candidates against a **short** query: about 28
   words, the title plus the first sentence.
3. **Gate: an absolute score.** At most two plan rows and two vault rows are emitted, each above a
   raw logit of −3, labelled `strong` at 0 or above and `likely` below it.

The findings that fixed this shape:

- **No vector index.** BM25 alone puts the right answer in the pool 15 times out of 15; a
  multilingual embedding managed 12. Leaving it out removes a model, an index build and most of the
  memory.
- **Separate indexes.** Sharing term statistics lets vault growth move plan-side ranking for no
  reason. With one index per corpus the plan side is unaffected by a tenfold larger vault.
- **A similarity score alone cannot abstain.** Embedding cosines sat between 0.76 and 0.93 whether
  the item belonged or not, so no threshold separated them. The cross-encoder's logit does.
- **The rerank query must be short.** Given the full 120-word lead, the cross-encoder scored every
  candidate at probability 1.00 on 5 of 12 items. The retrieval query and the rerank query differ on
  purpose.
- **The raw logit, not the sigmoid.** The probability saturates at both ends; the logit still moves
  when the model is merely unsure.
- **Two rows per corpus.** Precision is about 40% by the third row, so a third row costs a
  verification read and buys nothing.

At that gate, 16 of 20 in-scope test items get a pointer, both out-of-scope controls get none, and
about 88% of emitted rows are correct. Missing a pointer costs nothing and a wrong one costs trust,
so the cut is on the conservative side.

## The block

Appended after `## Links`, and before `## Problems` when there is one. A list, never a table.

```markdown
<!-- neighbours:begin -->

## Possible neighbours — unverified

Most likely destination file: `ML/Systems.org` (from the nearest neighbour below — a
guess about the *file*, which is a safer call than the task).

Machine retrieval, not a finding. These are the nearest passages a search over the
plans and the vault found; roughly one in eight is wrong, and a hit here is *not*
evidence the item is a duplicate. Judge `vet` first and on the item's own merits,
then open these to check whether they actually cover it. An item with a neighbour is
as likely to need a new task beside it as a merge into it.

- plan  `ML/Systems.org:324` likely — LLM inference and serving > Prefill-decode disaggregation and KV cache transfer between instances
- vault `ML & AI/Concepts/Detecting machine-generated text.md:6` strong — 1. Watermarking (generator-side)

<!-- neighbours:end -->
```

The block is written only when at least one row clears the gate. An absent section is information;
an empty one is noise.

**The hedged wording is part of the feature and must not be trimmed.** The identical rows under a
bare `## Related` heading cost 1.3% more tokens than no field at all and drove the agent to merge all
six test items. Worded as unverified candidates, they cut tokens by 15.5% and restored a correct
mixed set of verdicts. A tidied version regresses to worse than nothing.

**The destination hint is the file of the top plan row**, and is free. Deriving it from the file
charters was tried and fails (about 7 of 20 right), because a charter is too abstract to match a
concrete title. The top neighbour's file is right about nine times in ten, and the hint was worth
another large cut in tokens.

## Where it runs

On the laptop, inside `sync.py`, after the items and both views are written. An earlier design put
it on the server, fed by a pushed index. Dropping the embedding removed the only component expensive
enough to precompute anywhere, and what is left costs about a minute for 15 items on the laptop,
nearly all of it reranking. The laptop already holds both corpora, so there is no token, no polling,
no index transfer, and no copy of private notes on a machine that serves an unauthenticated
dashboard.

- `neighbours.py` does not import the daemon, and its dependencies (`sentence-transformers`, `torch`)
  are an optional extra that the server image never installs.
- `sync.py` imports it lazily, so a laptop without the extra still syncs and prints one line saying
  the pass was skipped.
- How the pass fits into a sync, including what happens when items are dropped while it runs, is in
  [`sync.md`](sync.md#the-annotation-pass).

## What the same measurements say about routing cost

These shaped how the inbox is used, not the code:

- About two thirds of a routing session's token bill is the fixed preamble re-read on every turn,
  not search. The lever is turns and batch size.
- Cost grows faster than linearly with batch size, so batches of four to eight items are cheaper
  than one large batch.
- The stronger model cost 1.6 times the smaller one on the same input, not the five times a
  per-token comparison suggests, because it read less, and its routing was visibly better.

Open questions are listed in [`plans.md`](../plans.md#related-notes).

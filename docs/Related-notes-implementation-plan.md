# Related notes — implementation plan

Hand this to a fresh session. The evidence behind every decision here is in
`docs/Related-notes-design.md`; the working code is in `prototypes/related-notes/`.
**Read the design document first.** This plan says what to build, not why.

## Ground rules

- Nothing in this feature may ever fail a sync or withhold an item. It is
  enrichment in the same sense the daemon's preprocessing steps are, and the
  delivery guarantee in `AGENTS.md` applies to it by analogy: a broken annotator
  produces an item without the section, never a sync that stops.
- No backward compatibility. Nothing has shipped; there is no old format.
- Do not add this to the daemon, to `config.yaml`, to the Docker image, or to any
  route's `steps` list. See §1.

## 0. Amendments made when this was implemented

Four, all decided with the user before any code was written. Everything else below
stands.

1. **The pass runs after delivery, not before it.** `sync.sh` writes the items and both
   views, prints that the inbox is ready to review, and only then searches for
   neighbours, reporting progress as it goes. The minute this costs is free: the user
   reads the queue himself for several minutes before handing anything to `/route`, and
   the block is for the agent, not for him. So he starts immediately and the pass
   finishes underneath him. Consequences, all load-bearing:
   - every index it needs is read into memory before the search starts, so his parallel
     edits are never read half-written;
   - nothing is written to an item that has since gone — checked, because
     `atomic_write_text` creates parents and would otherwise resurrect a directory he
     just dropped. A gone item is logged and skipped, and is not an error;
   - `triage.md` is re-rendered at the end, from disk, so the blocks reach the file
     `/route` actually reads.
2. **`info` only.** `job`, `clip` and `lang` are consumed by other scripts.
3. **`triage.md` alone is rewritten at the end.** `triage.org` renders from frontmatter,
   the block adds no field, and the user is looking at that buffer.
4. **`--regenerate` reuses the blocks already in the item directories** rather than
   recomputing, which is what keeps renumbering after a drop instant. `--neighbours`
   forces a fresh pass; `--no-neighbours` skips one.

## 1. Decision: this runs on the laptop, inside `sync.py`

`docs/Related-notes-design.md` §7 originally proposed a NAS pipeline step fed by a
laptop-pushed index. **That is superseded.** §3a showed the embedding model earns
nothing over BM25 on this corpus, which removes the only thing that was expensive to
compute and therefore the only reason to precompute it anywhere. What is left —
parse two corpora, build two BM25 indices, rerank 40 candidates per item — is 63
seconds on the laptop for 15 items, measured, and the laptop already holds both
corpora as live working copies.

So: no GitHub token, no polling, no index transfer, no vault mirror on a box that
serves an unauthenticated dashboard, and no daemon changes at all.

Insertion point is exact. In `sync.py:synchronize()` — **amended when built, see §0:
the annotation runs *after* the views, not before**:

```python
        _remove_stale_local_items(...)          # existing
        ...rsync -azc download...               # existing
    for item in remote_items: ...               # existing, manifest update
    write_manifest(...)                         # existing
    generate_inbox(config.local_inbox)          # existing, unchanged
    print("...ready to review now")             # existing line, reworded
    annotate_inbox(config)                      # NEW — this feature
```

`generate_inbox` is not touched. `triage.md` stays a pure concatenation of
`index.md` files, `triage.org` stays navigation-only, and the numbering contract is
untouched — the annotator has already run by the time either is rendered.

**Two writers now touch `index.md`**, the daemon's `index-render` and this. That is
a deliberate contract amendment and must be written into `AGENTS.md` (§7 below). It
is safe because the annotation is delimited, append-only, and fully regenerated on
every sync: strip any existing block, then append a fresh one. Note `sync.py`
downloads with `rsync -azc` — checksum comparison — so an annotated `index.md`
differs from the NAS copy and is re-downloaded next sync. That is not a bug; it
restores the pristine file and the annotator re-applies. Do not try to defeat it.

## 2. New module: `info_triage/neighbours.py`

One module, laptop-side, imported by `sync.py`. It must not import the daemon
(`config.py`, `storage.py`, `processing.py`) — `sync.py` is deliberately a separate
program and this keeps it that way.

Port from the prototype, do not re-derive:

| prototype file | what to take |
|---|---|
| `corpus.py` | `parse_org`, `parse_vault`, unit shape. This is the part most likely to need care — Org heading/ancestor parsing and the vault windowing with per-window line numbers. |
| `search.py` | `tokenize`, `BM25`. Pure Python, no dependency. |
| `rerank.py` | model load and `score()`. Note `activation_fn=torch.nn.Identity()` — raw logits, not the sigmoid. |
| `eval3.py` | `short()` — the short rerank query. Load-bearing; see design §3. |
| `destination.py` | **Do not port.** The charter approach measurably fails. Destination is the top neighbour's file. |

Public surface:

```python
@dataclass(frozen=True)
class Neighbour:
    source: str      # "plan" | "vault"
    file: str        # repo-relative
    line: int
    path: str        # breadcrumb
    title: str
    score: float     # raw cross-encoder logit

def build_index(org_root: Path, vault_root: Path) -> Index: ...
def neighbours_for(index: Index, item_text: str, k: int = 2) -> tuple[str | None, list[Neighbour]]:
    """Returns (destination_file, rows). destination_file is the top plan row's file."""
def annotate_inbox(local_inbox: Path, org_root: Path, vault_root: Path) -> None: ...
```

### Parameters, all measured — do not tune without re-measuring

- **Two BM25 indices, one per corpus.** Not one shared index. Design §6b: a shared
  index lets vault growth move Org-side IDF, and Org-side quality drops 11/15 → 9/15
  across a 10x vault. Separate indices make it exactly invariant.
- Candidate pool: 20 per corpus, 40 total, into the reranker.
- Emit **at most 2 plan rows and 2 vault rows**. Precision@3 is ~40%; rows beyond
  the second cost a verification read and buy nothing.
- Abstain cut: **logit ≥ −3.0**. Label `strong` at ≥ 0, `likely` below it.
- Retrieval query: item `title`/`headline` + `intent` + first 120 words of `## Lead`.
  Rerank query: `short()` of that. **These are different on purpose** — a long
  rerank query saturates the cross-encoder at 1.0 for every candidate.
- Model: `BAAI/bge-reranker-base`, `max_length=384`. Multilingual (the notes contain
  Russian and Spanish). No embedding model.

## 3. The section it writes

Append to each item's `index.md`, after `## Links`, before `## Problems` if present.
A list, never a table — same rule as `## Sources`.

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

**The caveat paragraph is not decoration and must not be trimmed.** Design §6: the
identical rows under a bare `## Related` heading cost 1.3% *more* tokens and drove
the agent to `MERGE` all six items. The reworded version cut tokens 15.5% and
restored correct mixed verdicts. If someone later "tidies" this prose, the feature
regresses to worse-than-nothing.

Emit the whole block only when at least one row clears the cut. No rows, no section —
an absent section is information, an empty one is noise.

## 4. Dependencies — keep them off the NAS

`sentence-transformers` and `torch` are large and are needed **only** on the laptop.
Add them as an optional extra, and leave the daemon's runtime dependency list alone:

```toml
[project.optional-dependencies]
neighbours = ["sentence-transformers>=5,<6", "torch>=2.4"]
```

The Docker image must not install this extra — check `Dockerfile` and
`docker-compose.yml` and confirm the NAS image is unchanged. `docker compose build`
must still succeed and must not grow.

`sync.py` imports `neighbours` lazily inside the annotation call so that a laptop
without the extra installed still syncs — it prints one line saying annotation was
skipped and carries on. That is the delivery guarantee applied here.

## 5. Configuration

`sync.py` has no config file by design (`default_config()` plus CLI flags). Follow
that. Add to `SyncConfig`:

```python
    org_root: Path | None = None      # ~/Library/.../notes/org
    vault_root: Path | None = None    # ~/Library/.../notes/obsidian
```

defaulted in `default_config()`, overridable by `INFO_TRIAGE_ORG_ROOT` /
`INFO_TRIAGE_VAULT_ROOT`. Add `--no-neighbours` to skip annotation. (Amended, §0: a
`--regenerate` reproduces the same file by concatenation, because the block is in each
`index.md` already; `--neighbours` recomputes when the plans have moved.)

If either root is missing or unreadable: print one line, skip annotation, continue.
Never raise.

## 6. Cost and what to do about it

Measured on an M1, 15 items, warm model cache:

| stage | time |
|---|---|
| parse both corpora (7,977 units) | 0.6 s |
| build two BM25 indices | 0.4 s |
| load reranker | 4.2 s |
| rerank 15 items × 40 candidates | 57.4 s |
| **total added to `sync.sh`** | **62.6 s** |

That is a minute, not "a few seconds". Build it the simple way first and measure
before optimising. If it needs to come down, in order of expected return:

1. **Skip unchanged work.** Cache each item's block keyed on
   `(item revision, org HEAD sha, vault HEAD sha)` under
   `~/.local/state/info-triage/`. Both note repos auto-commit every minute, so the
   shas are free to read. When the plans have not moved, only genuinely new items are
   reranked and a sync costs seconds. When they have, everything recomputes — which
   is the point of doing this at sync time.
2. Drop the pool from 40 to 24 (12 per corpus). Re-measure hit@k first.
3. Truncate candidate text from 1500 to 800 characters before reranking.

Do **not** reach for ONNX, quantisation or threading tricks. This is a laptop, once
a day, and the simple version is already inside the budget.

## 7. Documentation to update

- `AGENTS.md` — **Synchronization Contract**: state that `sync.py` annotates each
  local `index.md` with a delimited, regenerated neighbours block after download and
  before view generation; that `triage.md` remains a pure concatenation; and that the
  block is expected to be re-applied after every `rsync -azc`.
- `AGENTS.md` — **Current Prototype Contract**: `index.md` now has two writers.
- `docs/DESIGN.md` — only if the task is judged to change the architecture; this is
  a laptop-side addition to an existing script, so probably not.
- `docs/PREPROCESSING.md` — **no change**. This is not a preprocessing step and must
  not be listed as one.

## 8. Tests

`tests/` already covers sync. Add to the same style, and keep them offline — no model
download in the test suite.

- `parse_org`: heading levels, ancestor breadcrumb, TODO keyword extraction, drawer
  and `SCHEDULED:` stripping, charter extraction.
- `parse_vault`: section splitting, windowing of a long section, and that two windows
  of one section get **different** line numbers (this was a real bug in the prototype).
- `BM25`: a known ranking on a tiny fixture corpus.
- Section rendering: exact block shape, delimiters, `strong`/`likely` labels,
  abstention producing no section at all, and idempotency — annotating twice yields
  one block, not two.
- `annotate_inbox` with a fake scorer injected, so no model is loaded.
- `sync.synchronize` still completes when annotation raises.
- Existing sync tests must pass unchanged; `triage.md` for an unannotated item must
  be byte-identical to today's output.

## 9. Suggested session breakdown

1. `corpus` parsing + BM25 + their tests. No model, no sync changes. Verifiable alone.
2. Reranker wrapper, `neighbours_for`, section rendering + tests with an injected
   scorer.
3. Wire into `sync.py`: config fields, flags, lazy import, failure containment, and
   the `AGENTS.md` amendments.
4. Run it against the real inbox and both real corpora, eyeball 15 items, then decide
   whether §6's caching is needed.

## 10. Known-open, from the design document

- Every A/B behind this is n=1 per condition. The verdict patterns are stark enough
  to trust; the token percentages are not. Re-run before quoting them.
- The abstain cut is fitted to 22 hand-judged queries on an ML-heavy sample. Re-fit
  once the inbox has breadth.
- The vault side will likely need a stricter cut than the plan side as the vault
  grows. Re-measure when it has doubled, not before.

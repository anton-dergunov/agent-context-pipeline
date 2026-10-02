# Experiment · how much of a real link collection can be retrieved?

**Question.** An agent fetching saved links itself is refused about one time in ten, most often by
publishers and journals. How much of the same kind of material can the server retrieve and convert
to Markdown, and can it say why for the rest?

**Status.** Run 13 Aug 2026 over 97 URLs: **86 complete (89%)**, 11 failed, each failure with a
stable reason key. The extractors shipped as the pipeline's `content-extraction` step.

**Serves.** [`docs/extractors/documents.md`](../../docs/extractors/documents.md) and
[`docs/architecture/item-contract.md`](../../docs/architecture/item-contract.md), where the 11% is
why `extraction` is a first-class field.

## Method

- **Corpus.** The manifests in [`tests/fixtures/url_extraction/`](../../tests/fixtures/url_extraction/),
  drawn from the owner's notes: 50 ordinary web pages, the 14 PDF links that are not on arXiv, and 33
  research-paper links (20 arXiv, the rest spread over ACL Anthology, PMLR, OpenReview, CVF,
  NeurIPS, JMLR and one ResearchGate page).
- **Apparatus.** The shipped extractors, run sequentially with a one-second delay between requests:

  ```bash
  uv run python experiments/url-extraction-corpus/corpus.py
  ```

  It writes one directory per URL under the ignored `url_output/` and a `summary.json` beside them.
  Normal tests are offline; this is the live acceptance run.
- Expected network failures are recorded, not removed from the corpus.

## Results

| | |
|---|---|
| attempted | 97 |
| complete | 86 (89%) |
| failed | 11 |

| Route | Complete | Failed |
|---|---|---|
| generic document (HTML and PDF) | 53 | 8 |
| arXiv | 20 | 0 |
| ACL Anthology | 4 | 0 |
| PMLR | 3 | 0 |
| CVF, NeurIPS, JMLR | 2 each | 0 |
| OpenReview | 0 | 2 |
| ResearchGate | 0 | 1 |

Three links in the PDF manifest are provider PDFs and are routed to their research provider, which
is why the route counts differ from the manifest counts.

| Failure reason | Count |
|---|---|
| `metadata-unavailable` | 3 |
| `access-blocked` | 2 |
| `html-no-extractable-text` | 2 |
| `request-error` | 2 |
| `http-error` | 1 |
| `unsafe-url` | 1 |

What the failures are:

- The three `metadata-unavailable` are the two OpenReview papers, whose metadata endpoint did not
  return JSON, and the ResearchGate page, which answered 403. ResearchGate is anonymous best effort
  by design.
- `access-blocked` is a site answering 403.
- `html-no-extractable-text` is a page from which no readable text could be extracted.
- `request-error` is a host that no longer answers, and the one `http-error` is a 404.
- `unsafe-url` is the safety guard refusing a host that does not resolve exclusively to public
  addresses. It is a refusal by this project, not a failure of the site.

## Files

[`summary.json`](summary.json) holds the totals and one record per URL:
`{manifest_group, url, handler, provider, output, returncode, status, reason, error}`. `output` is
the directory relative to `url_output/`. The extracted bodies and raw sources (124 MB) are not
committed; the command above regenerates them.

## Limits

- One run, one day, one network. A refusal is a fact about that request.
- The vocabulary has since gained `partial` and `blocked`; this run predates them, so a refusal is
  recorded here as `failed` with `access-blocked`.
- "Complete" means a body was extracted, not that it was read and checked.

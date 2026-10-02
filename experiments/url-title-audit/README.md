# Experiment · how many bare links can be given a trustworthy title?

**Question.** A saved link with no title says nothing about what it is. How many of the links in a
real collection of notes can be titled from public page metadata alone, and what do the rest fail on?

**Status.** Run twice on 13 Aug 2026 over 529 URLs: **434 titled (82%)** with plain requests, then
**489 titled (92%)** after three changes to the resolver. Those changes shipped in commit `df7218b`.

**Serves.** [`docs/architecture/preprocessing.md`](../../docs/architecture/preprocessing.md#url-and-title-resolution).

## Method

- **Corpus.** [`tests/fixtures/url_titles.txt`](../../tests/fixtures/url_titles.txt): 529 unique
  HTTP(S) URLs collected from the owner's notes and from earlier extractor output. It spans
  publishers, journals, shortened and affiliate links, PDFs, social pages and dead hosts.
- **Apparatus.** The shipped resolver, `info_triage/utilities/url_resolution.py`, run sequentially
  with a 15-second timeout and no retries. The opt-in test is the reproducible form:

  ```bash
  URL_TITLES_LIVE=1 uv run pytest tests/utilities/test_url_titles_live.py -q -s
  ```

  The same audit over any Markdown file is `info-triage-resolve-urls --report report.json`.
- A link counts as titled only when a trustworthy title was found: page metadata for HTML, document
  metadata or the first page's prominent text for a PDF. File names and body text are never used.

## Results

| Outcome | First run | After the changes |
|---|---|---|
| **resolved with a title** | **434** | **489** |
| `http-error` | 52 | 27 |
| `title-not-found` | 22 | 8 |
| `response-too-large` | 13 | 0 |
| `request-error` | 3 | 2 |
| `redirect-loop` | 2 | 0 |
| `destination-not-found` | 2 | 2 |
| `unsafe-url` | 1 | 1 |
| total | 529 | 529 |

The three changes between the runs:

1. **The HTML inspection cap went from 2 MiB to 20 MiB.** All 13 `response-too-large` pages resolved
   once the cap was raised.
2. **One anonymous retry with a Chrome-compatible HTTP client** when an ordinary request is blocked
   or returns something unusable. 26 of the 52 `http-error` links resolved; many publishers refuse a
   default client and answer a browser-shaped one. It runs no browser and no JavaScript, and never
   authenticates.
3. **A PDF with no document metadata gets its title from the first page's prominent text.** 16 of
   the 22 `title-not-found` links resolved, six of them PDFs and the rest shortened links and social
   pages that the retry reached.

Per-URL changes between the two runs:

| First run | Second run | URLs |
|---|---|---|
| resolved | resolved | 432 |
| `http-error` | resolved | 26 |
| `title-not-found` | resolved | 16 |
| `response-too-large` | resolved | 13 |
| `request-error` or `redirect-loop` | resolved | 2 |
| any failure | still a failure | 38 |
| resolved | `title-not-found` | 2 |

Two links that had a title in the first run lost it in the second. That is a regression of two
against a gain of 57, and it was not investigated.

A second, focused set of 30 cases, 25 of them failures of the first run, was re-run after the
changes: 24 titled, 4 `http-error`, 2 `title-not-found`.

The 40 links still untitled are mostly refusals no anonymous client gets past, pages that have gone,
and hosts that no longer resolve. They stay in an item as bare URLs with a stable reason, and the
item is delivered either way.

## Files

| File | Holds |
|---|---|
| [`report.json`](report.json) | first run: the summary and one record per URL |
| [`report-improved.json`](report-improved.json) | the same corpus after the changes |
| [`problem-cases-report-improved.json`](problem-cases-report-improved.json) | the 30 focused cases, after the changes |
| [`problem-cases-improved.md`](problem-cases-improved.md) | the same 30 as the Markdown the resolver writes |

Each record is `{source_url, final_url, title, reason, error}`. The URL caches and the enriched
copies of the notes themselves are not committed.

## Limits

- One run per configuration, on one day, from one residential connection. Refusals vary by network
  and by day.
- "Trustworthy" was judged by rule, not by reading all 489 titles. A page that titles itself "Blog"
  counts as titled.

# Web pages, PDFs and research papers

The generic `document` handler converts ordinary public HTML and PDF, and the `research` handler
normalizes papers from known providers. Both are reached through `url-extract`; see
[`routing.md`](routing.md) for the dispatcher and the shared output shape.

```bash
uv run url-extract https://example.org/article
uv run url-extract https://example.org/report.pdf
uv run url-extract https://arxiv.org/pdf/2003.00911
```

## Generic documents

HTML is converted with Trafilatura. PDF conversion uses PDFPlumber with a font-relative word-gap
threshold and never performs OCR; pypdf's ordinary-flow text is the per-page fallback for unusual
encodings and duplicate embedded glyph layers. The adaptive threshold avoids the missing-space
failure common in tightly kerned research PDFs. A scanned or image-only PDF gets the stable failure
reason `pdf-no-extractable-text`.

Default limits are a 30-second read timeout, a 10 MiB HTML response, a 50 MiB PDF response, 500 PDF
pages, two retry attempts, and sequential extraction.

```text
url_output/html/<stable-url-id>/        url_output/pdf/<stable-url-id>/
  content.md                              content.md
  metadata.json                           metadata.json
  status.json                             status.json
  raw/source.html                         raw/source.pdf
```

## Research papers

Dedicated normalization supports arXiv, ACL Anthology, PMLR, OpenReview, CVF Open Access, NeurIPS
Proceedings, JMLR, and anonymous best-effort ResearchGate. A publication page and its PDF map to one
provider and paper identity.

For arXiv, `/abs`, `/html` and `/pdf` inputs — including an accidental `.pdf` suffix on `/abs` —
first fetch the abstract page. Experimental paper HTML is preferred for conversion and the PDF is
the automatic fallback. When the HTML body succeeds, the PDF is fetched anyway and kept, because it
is the readable artifact for a person even though it is the worse conversion source. That extra
fetch is best effort: if it fails, the attempt is recorded and the extraction stays `complete`.

OpenReview metadata uses API v2 followed by the legacy v1 API. Other open providers normalize to
their publication page and linked PDF. ResearchGate uses anonymous ordinary HTTP and one anonymous
Chrome-compatible retry only; it does not import cookies or try to bypass access controls.

Output is written under `url_output/research/<provider>-<paper-id>/`. `content.md` starts with
normalized metadata and the abstract, followed by the extracted paper. Under `raw/`, the PDF is
always `paper.pdf` and the HTML always `paper.html`, whichever the body was converted from, so
finding the readable copy never depends on which path the extraction took. Affiliations are not
parsed separately. If metadata succeeds but the body does not, `content.md` is kept and
`status.json` reports `partial` with `paper-body-unavailable`.

In an item's `index.md`, a paper's lead is its complete abstract and its PDF is linked from
`## Sources`.

## Live acceptance corpus

Normal tests are offline. The manifests under `tests/fixtures/url_extraction/` hold 50 ordinary
pages, 14 PDF links, 20 arXiv papers, two examples per additional open provider, and one
ResearchGate page. Run them sequentially with a one-second delay between requests:

```bash
uv run python experiments/url-extraction-corpus/corpus.py
```

Results land in the ignored `url_output/` with a `summary.json` of outcomes by route and stable
failure reason. The recorded run, 86 of 97 complete, is in
[`experiments/url-extraction-corpus/`](../../experiments/url-extraction-corpus/README.md).

# URL and research-paper extraction

`url-extract` is the standalone, single-URL entry point for content retrieval.
It is deliberately not part of Telegram processing yet.

## Routing

The dispatcher checks strict parsers in this order:

1. Instagram, LinkedIn, and YouTube
2. known research providers
3. Medium, including validated custom-domain story URLs
4. ordinary public HTML or PDF

A recognized shortener is resolved after the first specialized pass and its
destination is routed once more. Generic network retrieval validates every
redirect destination and refuses credentials, non-HTTP schemes, and hosts that
do not resolve exclusively to public IP addresses.

Use the normal defaults:

```bash
uv run url-extract https://arxiv.org/pdf/2003.00911
uv run url-extract https://example.org/article
uv run url-extract https://example.org/report.pdf
```

The unified command owns `--output-dir` and forwards every argument after `--`
to the selected extractor. This keeps specialized options out of a large shared
parser:

```bash
uv run url-extract https://arxiv.org/abs/2010.00747 -- --no-keep-raw
uv run url-extract https://youtu.be/WZxbzEpdjlU -- --skip-comments --skip-ocr
uv run url-extract --show-route https://aclanthology.org/2025.acl-long.461.pdf
```

The existing platform commands remain available for batch inputs and detailed
workstation configuration.

## Generic documents

HTML is converted with Trafilatura. PDF conversion uses PDFPlumber with a
font-relative word-gap threshold and never performs OCR; pypdf ordinary-flow
text is the per-page fallback for unusual encodings and duplicate embedded
glyph layers. The adaptive threshold avoids the missing-space failure common in
tightly kerned research PDFs. A scanned/image-only PDF receives the stable
`pdf-no-extractable-text` failure reason.

Default limits are a 30-second read timeout, 10 MiB HTML response, 50 MiB PDF
response, 500 PDF pages, two retry attempts, and sequential extraction. A
generic item contains:

```text
url_output/html/<stable-url-id>/
  content.md
  metadata.json
  status.json
  source.html

url_output/pdf/<stable-url-id>/
  content.md
  metadata.json
  status.json
  source.pdf
```

Raw sources are retained by default; pass `--no-keep-raw` after `--` to omit
them. Writes use temporary sibling files and atomic replacement.

## Research papers

Dedicated normalization supports arXiv, ACL Anthology, PMLR, OpenReview, CVF
Open Access, NeurIPS Proceedings, JMLR, and anonymous best-effort ResearchGate.
Publication-page and PDF routes map to one provider/paper identity.

For arXiv, `/abs`, `/html`, and `/pdf` inputs—including an accidental `.pdf`
suffix on `/abs`—first fetch the abstract page. Experimental paper HTML is
preferred and the PDF is the automatic fallback. OpenReview metadata uses API
v2 followed by the legacy v1 API. Other open providers normalize to their
publication page and linked PDF. ResearchGate uses anonymous ordinary HTTP and
one anonymous Chrome-compatible retry only; it does not import cookies or try
to bypass access controls.

Research output is written under
`url_output/research/<provider>-<paper-id>/`. `paper.md` starts with normalized
metadata and an abstract, followed by the extracted full paper. Affiliations
are not separately parsed. If metadata succeeds but the body does not,
`paper.md` is retained and `status.json` reports `partial` with
`paper-body-unavailable`.

## Live acceptance corpus

Normal tests are offline. The explicit manifests under
`tests/fixtures/url_extraction/` contain 50 ordinary pages, all 14 non-arXiv PDF
routes from the supplied `Unsorted.org`, 20 arXiv papers, two examples per
additional open provider, and the supplied ResearchGate page.

Run them sequentially with a one-second inter-request delay:

```bash
uv run url-extraction-corpus
```

Results remain in ignored `url_output/{html,pdf,research}/` directories.
`url_output/summary.json` reports outcomes by route/provider and stable failure
reason. Expected network failures are recorded rather than removed from the
corpus.

The library function `info_triage.extractors.router.route_url()` returns the
handler, canonical identity, source/routed URLs, and typed provider reference.
That is the future Telegram integration seam; this feature does not change the
daemon pipeline or accept local file inputs.

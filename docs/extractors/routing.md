# Extractors and URL routing

An extractor retrieves one link and converts it to Markdown. Each is a standalone command-line tool,
and each is also a handler of the pipeline's `content-extraction` step
([`preprocessing.md`](../architecture/preprocessing.md#content-extraction)).

| Handler | Command | Guide |
|---|---|---|
| any URL | `url-extract` | this page |
| HTML, PDF, research papers | `url-extract`; `research-extract` for papers alone | [`documents.md`](documents.md) |
| Instagram | `instagram-extract` | [`instagram.md`](instagram.md) |
| LinkedIn | `linkedin-extract` | [`linkedin.md`](linkedin.md) |
| Medium | `medium-extract` | [`medium.md`](medium.md) |
| YouTube | `youtube-extract` | [`youtube.md`](youtube.md) |

Extractors retrieve and convert. They do not summarize, and no language model runs in this path;
the reasons are in [`item-contract.md`](../architecture/item-contract.md#why-nothing-is-summarized).

## Routing

`src/info_triage/extractors/router.py:route_url()` is the only routing authority. It checks strict
parsers in this order:

1. Instagram, LinkedIn and YouTube
2. known research providers
3. Medium, including validated custom-domain story URLs
4. ordinary public HTML or PDF, handled by the generic `document` handler

A recognized shortener is resolved once, after the first specialized pass, and its destination is
routed once more. The function returns the handler, the canonical identity, the source and routed
URLs, and a typed provider reference.

## `url-extract`

The single-URL dispatcher. It owns `--output-dir` and forwards everything after `--` to the selected
extractor, which keeps specialized options out of one large shared parser:

```bash
uv run url-extract https://arxiv.org/pdf/2003.00911
uv run url-extract https://example.org/article
uv run url-extract https://arxiv.org/abs/2010.00747 -- --no-keep-raw
uv run url-extract https://youtu.be/WZxbzEpdjlU -- --skip-comments --skip-ocr
uv run url-extract --show-route https://aclanthology.org/2025.acl-long.461.pdf
```

The platform commands remain available for batch inputs and detailed configuration.

## Network safety

Retrieval validates every redirect destination and refuses credential-bearing URLs, non-HTTP
schemes, and hosts that do not resolve exclusively to public IP addresses. These guards are not to
be weakened.

## The shared output shape

Every extractor writes the same directory, whichever it is:

```text
content.md       the converted body
comments.md      where comments apply
metadata.json    the handler's own normalized fields
status.json      complete | partial | blocked | failed, with a stable reason key
raw/             everything retrieved but not converted
```

The names live in `extractors/artifacts.py`. There are no per-extractor file names and no adapter
layer between extractors and the pipeline. Raw sources are kept unless `--no-keep-raw` is given.
Writes use temporary sibling files and atomic replacement.

A refused request (401, 403, 429) is `blocked`, not `failed`: a refusal is a fact about the source,
while a failure only says this attempt did not work.

An extractor's comment policy, and the links it offers for one further round of retrieval, both live
in its `prepare.py`, in one function each, so what leads `comments.md` and what the pipeline follows
cannot disagree. Every retrieved comment stays on disk whatever the policy says about it.

## Changing an extractor

Retrieval, OCR and transcription behaviour is tuned against real material; the measurements are in
[`experiments/`](../../experiments/README.md). Output can be restructured freely. How an extractor
retrieves and recognizes changes only with a measured reason.

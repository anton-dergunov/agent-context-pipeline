# LinkedIn extraction

`linkedin-extract` downloads the text, content images, and the comments that LinkedIn includes in the anonymous public HTML for a post. It does not use a LinkedIn account, API token, browser cookies, browser automation, JavaScript, OCR, or an outbound URL resolver. It runs standalone and as the `linkedin` handler of the pipeline's `content-extraction` step.

## Important access note

Public visibility does not grant permission to crawl LinkedIn. LinkedIn's [crawling terms](https://www.linkedin.com/legal/crawling-terms) state that automated collection requires express permission, even though LinkedIn separately supports [embedding eligible public posts off-site](https://www.linkedin.com/help/linkedin/answer/a529065/embed-content-from-the-linkedin-feed). This tool is deliberately sequential and intended for personal, very low-volume use. LinkedIn may block access or change the public markup without notice.

The extractor never imports or sends login state. If LinkedIn returns an auth wall, checkpoint, block response, or page without the requested public post, the URL fails explicitly. It does not rotate proxies, open a browser, log in, or attempt another access path.

## Setup and usage

```bash
UV_CACHE_DIR=.uv-cache uv sync

UV_CACHE_DIR=.uv-cache uv run linkedin-extract \
  --input-file tests/fixtures/linkedin_urls.txt

UV_CACHE_DIR=.uv-cache uv run linkedin-extract \
  'https://www.linkedin.com/posts/USER_ACTIVITY-share-123-SUFFIX/'
```

Useful options:

```text
--output-dir PATH       default: linkedin_output
--max-comments N        keep at most N comments from the public page; default: 50
--request-delay SECONDS wait between post requests; default: 1.0
--timeout SECONDS       per-request read timeout; default: 30
```

Input files contain one URL per line. Empty lines and lines beginning with `#` are ignored. Tracking query parameters on the input post URL are not sent. Duplicate numeric post IDs are processed once.

## Output

Each post is written to `linkedin_output/<numeric-id>/`:

```text
content.md                   the displayed post body plus a LINKS section
comments.md                  the author's own comments, then everything else
metadata.json                normalized author, URNs, counts, links, and media
status.json                  complete, partial, blocked, or failed, with a reason
raw/comments.json / .txt     incomplete anonymous public comment selection
raw/metadata_raw.json        matching JSON-LD and extraction provenance
raw/media/                   post images and link-preview thumbnails
raw/response.html            retained only when the fetch or parse failed
```

LinkedIn normally exposes only selected comments to anonymous visitors. `raw/comments.json` records both the total count reported by the page and the number actually returned, marks the result as incomplete, and separately preserves the first returned comment.

`comments.md` leads with the comments the post's own author left, matched on their profile URL and falling back to their display name. This is not cosmetic ordering: on this platform the paper or the repository is regularly in the author's own first comment while the rest of the thread is engagement filler. Nothing is discarded — every returned comment is kept, below the author's.

URLs are not resolved. In particular, `lnkd.in` values remain shortened. The extractor may unwrap a LinkedIn tracking redirect when its query string already contains the visible short URL, but it never requests the short URL or its destination.

Videos, documents, private/login-only posts, newsletters, long-form LinkedIn articles, outbound linked-page text, OCR, and transcription are outside this command's scope.

## Live validation

The normal test suite uses small local HTML fixtures. The optional live test uses the public URLs in `tests/fixtures/linkedin_urls.txt` and writes only to pytest's temporary directory:

```bash
LINKEDIN_LIVE=1 UV_CACHE_DIR=.uv-cache uv run pytest \
  tests/extractors/linkedin/test_live.py -q
```

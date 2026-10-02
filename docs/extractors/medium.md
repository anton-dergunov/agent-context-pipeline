# Medium extraction

`medium-extract` retrieves a Medium article as Markdown. It runs standalone and as the `medium`
handler of the pipeline's `content-extraction` step. Which access routes work, and how much of a
member-only story is available, is measured in
[`experiments/medium-access/`](../../experiments/medium-access/README.md).

## Access note

Medium's [Rules](https://policy.medium.com/medium-rules-30e5502c4eb4) prohibit automated access
without express written consent, and low volume or a paid membership creates no exception. This tool
is for personal, very low-volume use: requests are sequential and direct, and it never accepts a
password, solves a CAPTCHA, rotates proxies, creates accounts or retries aggressively.

## Retrieval strategy

1. Check the article's Medium RSS feed, and stop if it holds either the full story or a member
   preview.
2. If RSS misses or fails, fetch Medium's story JSON and the article page with a Chrome-compatible
   TLS and HTTP fingerprint. Plain clients are refused with HTTP 403.
3. Keep whichever direct representation yields more useful Markdown.
4. Keep `sk` Friend Link tokens, and use a local browser session or a mounted cookie file for access
   the reader already has.

An article published under a publication does not identify its author's feed. When the author is
known, their feed can be supplied explicitly with `--feed-url`.

## Usage

```bash
uv sync
uv run medium-extract --input-file tests/fixtures/medium_urls.txt
```

The default method list is `rss,browser`. The first successful result wins, including an RSS member
preview. Change or reverse the order with `--methods`:

```bash
uv run medium-extract --methods rss 'ARTICLE_URL'
uv run medium-extract --methods browser 'ARTICLE_URL'
uv run medium-extract --methods browser,rss 'ARTICLE_URL'
```

Method names cannot be repeated, and an unknown method fails before any network access.

## Output

Each article gets a directory under `medium_output/<article-id>/`:

```text
content.md             the article as Markdown
metadata.json          title, author, dates, tags, source, and a full or preview label
status.json            the ordered methods, the attempts, the winning method, the status
raw/response.html      the browser method's page response, when there was one
raw/article.html       the exact RSS fragment, when RSS was used
raw/metadata_raw.json  Medium's structured story payload
```

| Status | Meaning |
|---|---|
| `complete` | the full article |
| `partial`, reason `medium-member-preview` | the title and a few opening paragraphs: enough to identify and file the article, not enough to judge it |
| `blocked`, reason `access-blocked` | the request was refused |
| `failed` | no method could reach the article at all |

## Membership authentication

On a workstation where Chrome is already signed in to Medium:

```bash
uv run medium-extract --methods browser --cookies-from-browser chrome 'ARTICLE_URL'
```

The browser-only method matters here, because an RSS preview counts as a successful first result in
the default list. This reads the local browser cookie store and can prompt for the macOS Keychain.
It does not read or store the Medium password.

For the server, export the Medium cookies from a logged-in browser as a standard Netscape
`cookies.txt` or as Playwright-style JSON. Export all `medium.com` cookies, not only analytics ones:

```json
{
  "cookies": [
    {
      "name": "sid",
      "value": "REDACTED",
      "domain": ".medium.com",
      "path": "/",
      "secure": true,
      "httpOnly": true
    }
  ]
}
```

A session cookie is effectively an account credential. Store the file outside the repository with
mode `0600`, mount it read-only into the container, and pass only its path:

```bash
MEDIUM_COOKIE_FILE=/run/secrets/medium-cookies.json \
  uv run medium-extract --methods browser 'ARTICLE_URL'
```

In the pipeline the same file is named by `extractors.medium.cookie_file` in `config.yaml`, unset by
default. If Medium does not accept the session the result stays a preview. Cookie values are never
written to logs or metadata. Logging out or revoking sessions invalidates the export.

Friend Links are simpler: if the URL contains `?sk=...`, the extractor keeps that parameter and
processes whatever access Medium grants to it.

## Converting saved HTML

HTML obtained separately can be converted without any request:

```bash
uv run medium-html-to-markdown saved-page.html --source-url 'https://medium.com/...' \
  --output content.md
```

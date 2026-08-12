# Medium Extraction Research

Research performed on 12 August 2026. This is a technical and contractual risk
assessment, not legal advice.

## Retrieval strategy

Info Triage uses a layered, sequential retrieval strategy:

1. Check the derived Medium RSS feed and stop if it contains either a full story or a
   member preview.
2. If RSS misses or fails, fetch Medium's story-ID JSON response and normal article
   page with a Chrome-compatible TLS/HTTP fingerprint.
3. Keep whichever direct representation yields more useful Markdown.
4. Retain `sk` Friend Link tokens and support a local browser session or mounted cookie
   file for access already granted to the user.

The utility never accepts a password. It does not solve CAPTCHAs, rotate proxies, create
accounts, or retry aggressively. Requests are sequential and direct.

Medium's [Rules](https://policy.medium.com/medium-rules-30e5502c4eb4) prohibit using
software or automated processes to access, scrape, or copy the service unless Medium
has given express written consent. They also require access through Medium's currently
available published interfaces. Low volume and a paid membership do not create an
exception in those rules. Medium's
[Terms of Service](https://help.medium.com/hc/en-us/articles/213481318-Medium-Terms-of-Service)
also say not to copy or download content unless the user has the right to do so and
grant only a limited personal licence to use the service.

Medium also documents RSS as an outbound interface for profiles, publications, custom
domains, and topics. Its
[RSS documentation](https://help.medium.com/hc/en-us/articles/214874118-Using-RSS-feeds-of-profiles-publications-and-topics)
explicitly says paywalled stories are not available there in full.

## What Medium's RSS feeds are

These are all feeds generated and served by Medium. They are not independent blog
services that Info Triage happens to scrape:

| Medium surface | Feed shape |
| --- | --- |
| Author profile `medium.com/@name` | `medium.com/feed/@name` |
| Username subdomain `name.medium.com` | `name.medium.com/feed` |
| Publication `medium.com/publication` | `medium.com/feed/publication` |
| Medium-hosted custom domain | `custom-domain.example/feed` |
| Topic or publication tag | Documented `/feed/...` variant |

A story published in a publication belongs to its author and normally appears in both
the author's rolling feed and the publication's rolling feed. They have independent
windows: a busy publication can push a story out quickly while it remains in a less
active author's feed. Medium does not document a permanent per-story RSS endpoint.

Each RSS item takes one of two relevant forms:

- Public story: `content:encoded` contains the complete article HTML.
- Paywalled story: `description` contains a preview card, usually an image and one
  sentence followed by “Continue reading.”

This explains the original uneven result. The two Leading Indicator articles were
still in a relatively quiet publication feed and were public, so both full bodies were
present. The member stories had either rolled out or were deliberately reduced to a
feed snippet. The Qwen story had rolled out of Mac O'Clock's busy feed but remained in
its author's feed.

## What is technically available

| Route | Public story | Member-only story | Suitable here |
| --- | --- | --- | --- |
| Documented RSS feed | Full HTML when the story remains in the rolling feed | Title, image, and normally a one-sentence preview | First |
| Browser-compatible story HTML/JSON | Full HTML and structured paragraphs | Several opening paragraphs anonymously; full body with an authorized session | Second |
| Official Medium API | No read-story endpoint | No read-story endpoint | No |
| Medium mobile offline mode | Full story for a signed-in member | Full story for a signed-in member | Manual use only; no Markdown export |
| Manually supplied HTML | Whatever the user lawfully supplies | Whatever the user lawfully supplies | Yes; conversion makes no network request |

Medium's [API/Importing notice](https://help.medium.com/hc/en-us/articles/213480228-API-Importing)
says it issues no new integration tokens. The archived official API documentation has
[profile, publication, publishing, and image-upload resources](https://github.com/Medium/medium-api-docs),
but no endpoint for reading arbitrary articles. An old integration token therefore
would not solve this use case.

Plain curl, Requests, and headless Chrome received Cloudflare HTTP 403 responses during
testing even with Chrome headers. `curl-cffi` succeeded because it reproduces Chrome's
TLS and HTTP/2 fingerprint rather than only its `User-Agent`. This distinction is why a
normal browser can work while superficially similar automation fails.

## Supplied URL results

The complete live extraction produced these results:

| Article ID | Result |
| --- | --- |
| `dc54b0db5d04` | Anonymous preview: title and opening paragraphs, about 220 words |
| `2e2cf411d4f9` | Anonymous preview: title and opening paragraphs, about 216 words |
| `1ec4b5bcec35` | Anonymous preview: title and opening paragraphs, about 264 words |
| `dc2e9c207d00` | Full article, about 1,544 words |
| `f067d56bed54` | Full article, about 1,521 words |

For the two public stories, RSS and browser extraction were compared after removing
Markdown markers and normalizing whitespace. Both comparisons had identical word
counts, sequence similarity `1.0000`, and vocabulary similarity `1.0000`. Their small
raw-character differences come from Markdown heading/formatting choices, not missing
article content. RSS is therefore the better first method for these cases.

The five URLs are kept in `tests/fixtures/medium_urls.txt`. Generated bodies live under
the ignored `medium_output/` directory rather than being committed as test fixtures.
Every result now has a non-empty `article.md`.

An article URL published under a publication does not identify its author feed. If the
author is known independently, an explicit documented feed can be supplied. For the
Qwen sample, the author feed currently returns the member preview:

```bash
uv run medium-extract \
  --feed-url 'https://medium.com/feed/@manjunath.shiva' \
  'https://medium.com/macoclock/qwen3-6-35b-runs-on-a-16-gb-m4-mac-mini-fully-in-memory-no-tricks-1ec4b5bcec35'
```

## Usage

Install the pinned environment and process URLs sequentially:

```bash
uv sync
uv run medium-extract --input-file tests/fixtures/medium_urls.txt
```

The default method list is explicitly `rss,browser`. The first successful result wins,
including an RSS member preview. Customize or reverse the order with `--methods`:

```bash
uv run medium-extract --methods rss 'ARTICLE_URL'
uv run medium-extract --methods browser 'ARTICLE_URL'
uv run medium-extract --methods browser,rss 'ARTICLE_URL'
```

Method names cannot be repeated and an unknown method fails before network access.
This ordered list is intentionally represented independently of the CLI so it can be
moved into the future project-wide processor/extractor configuration without changing
the downloader.

Each article ID gets a status file. Available items also get:

```text
response.html      browser-method page response when available
article.html       exact RSS full/preview fragment when RSS was used
article.md         Trafilatura Markdown output
metadata.json      title, author, dates, tags, source, and full/preview label
metadata_raw.json  structured Medium story payload
status.json        configured ordered methods, attempts, winning method, and outcome
```

## Membership authentication

On a workstation where Chrome is already signed in to Medium:

```bash
uv run medium-extract --methods browser --cookies-from-browser chrome 'ARTICLE_URL'
```

The explicit browser-only method matters here because an RSS preview counts as a
successful first result in the default method list.

This reads the local browser cookie store; it can prompt for the macOS Keychain. It
does not read or store the Medium password.

For the NAS, export the Medium cookies from a logged-in browser as either a standard
Netscape `cookies.txt` or Playwright-style JSON. Export all `medium.com` cookies, not
only analytics cookies. The JSON shape is:

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

Other session cookies can be included as additional objects. Do not paste this file
into chat or commit it. Store it outside the repository with mode `0600`, mount it
read-only into the container, and pass only its path:

```bash
MEDIUM_COOKIE_FILE=/run/secrets/medium-cookies.json \
  uv run medium-extract --methods browser 'ARTICLE_URL'
```

The status remains `preview` if Medium does not accept or authorize the supplied
session. A successful membership response is labeled `full`; cookie values are never
written to logs or article metadata. Logging out or revoking sessions can invalidate
the export, so periodic re-export may be necessary.

Friend Links are simpler. If the author-provided URL contains `?sk=...`, the extractor
preserves that parameter and processes whatever full access Medium grants to it.

Convert HTML that was obtained separately and lawfully without making a request:

```bash
uv run medium-html-to-markdown saved-page.html --source-url 'https://medium.com/...' \
  --output article.md
```

## Search indexes and original sources

Search engines often index the same anonymous opening section and may retain a longer
snippet for a while. This helped validate the Qwen preview, but search snippets are
unstable, fragmented, and do not provide a dependable article API, so the utility does
not reconstruct articles from search-result pages.

The structured Medium metadata exposes `canonicalUrl` and `importedUrl`. If an author
cross-posted or imported a story from a personal site, that URL can be retrieved as a
separate source in a future fallback. For these five examples, all canonical URLs point
to Medium and every `importedUrl` is empty. Related LinkedIn posts and model cards exist
for the Qwen work, but they are supporting sources rather than copies of the article.

A Medium session cookie is effectively an account credential and may expire or be
revoked. The other useful access routes are Medium's own offline-reading feature, an
author-provided copy, or an author/Friend link.

For UK users, the Intellectual Property Office's
[copyright exceptions guidance](https://www.gov.uk/guidance/exceptions-to-copyright)
says non-commercial research/private study can permit limited extracts when the use is
fair, but copying a whole work would not generally be fair dealing. Amount, purpose,
market substitution, acknowledgement, and the applicable jurisdiction all matter.

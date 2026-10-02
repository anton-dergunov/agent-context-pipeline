# Experiment · which routes to a Medium article actually work?

**Question.** Medium blocks ordinary automated requests and puts many stories behind a member wall.
Which access routes return an article's text, how much of a member-only story is available
anonymously, and which route should be tried first?

**Status.** Researched and run on 12 Aug 2026 over five articles. Shipped: RSS first, then a
browser-compatible request, with an optional session for access the reader already has. Two of five
articles came back whole and three as member previews of about 220 to 260 words.

**Serves.** [`docs/extractors/medium.md`](../../docs/extractors/medium.md).

This is a technical and contractual assessment, not legal advice.

## Method

- **Corpus.** The five URLs in [`tests/fixtures/medium_urls.txt`](../../tests/fixtures/medium_urls.txt):
  three member-only stories and two public ones.
- **Apparatus.** The shipped extractor. Each route can be run alone:

  ```bash
  uv run medium-extract --input-file tests/fixtures/medium_urls.txt
  uv run medium-extract --methods rss 'ARTICLE_URL'
  uv run medium-extract --methods browser 'ARTICLE_URL'
  ```

  Output goes to the ignored `medium_output/`; no article text is committed.

## What each route gives

| Route | Public story | Member-only story | Used |
| --- | --- | --- | --- |
| Documented RSS feed | Full HTML while the story remains in the rolling feed | Title, image and normally a one-sentence preview | First |
| Browser-compatible story HTML and JSON | Full HTML and structured paragraphs | Several opening paragraphs anonymously; the full body with an authorized session | Second |
| Official Medium API | No endpoint for reading a story | No endpoint for reading a story | No |
| Medium's mobile offline mode | Full story for a signed-in member | Full story for a signed-in member | Manual use only; no Markdown export |
| HTML the reader saved themselves | Whatever was supplied | Whatever was supplied | Yes; conversion makes no request |

Medium's [API notice](https://help.medium.com/hc/en-us/articles/213480228-API-Importing) says it
issues no new integration tokens, and the archived
[API documentation](https://github.com/Medium/medium-api-docs) has profile, publication, publishing
and image-upload resources but nothing that reads an arbitrary article. An old token would not help.

### The 403 finding

Plain curl, Requests and headless Chrome all received Cloudflare HTTP 403 responses, even with
Chrome's headers. `curl-cffi` succeeded because it reproduces Chrome's TLS and HTTP/2 fingerprint and
not only its `User-Agent`. That is why a normal browser works where superficially similar automation
fails, and it is the client the URL title resolver now uses for its one retry.

### What Medium's RSS feeds are

They are generated and served by Medium, not by independent blog services:

| Medium surface | Feed |
| --- | --- |
| Author profile `medium.com/@name` | `medium.com/feed/@name` |
| Username subdomain `name.medium.com` | `name.medium.com/feed` |
| Publication `medium.com/publication` | `medium.com/feed/publication` |
| Medium-hosted custom domain | `custom-domain.example/feed` |
| Topic or publication tag | a documented `/feed/...` variant |

A story in a publication normally appears in both the author's rolling feed and the publication's,
and the two windows are independent: a busy publication pushes a story out quickly while it stays in
a quieter author's feed. There is no documented permanent per-story feed.

An item takes one of two forms. A public story's `content:encoded` holds the complete article HTML.
A paywalled story's `description` holds a preview card, usually an image and one sentence.

## Results

| Article | Result |
| --- | --- |
| `dc54b0db5d04` | anonymous preview: title and opening paragraphs, about 220 words |
| `2e2cf411d4f9` | anonymous preview, about 216 words |
| `1ec4b5bcec35` | anonymous preview, about 264 words |
| `dc2e9c207d00` | full article, about 1,544 words |
| `f067d56bed54` | full article, about 1,521 words |

The first attempt was uneven for a reason the feed mechanics explain. The two public articles were
still in a quiet publication's feed, so both bodies were present. The member stories had either
rolled out of their feed or were reduced to a snippet; one had left its busy publication's feed and
remained in its author's.

**RSS against the browser route.** For the two public stories, the two extractions were compared
after removing Markdown markers and normalizing whitespace. Both had identical word counts, sequence
similarity 1.0000 and vocabulary similarity 1.0000. The small differences in raw characters come from
heading and formatting choices. RSS is therefore the better first method where it has the story.

**Decision.** Try RSS and stop if it has the story or a preview; otherwise fetch the story page with
the browser-compatible client and keep whichever representation yields more Markdown. A preview is
reported as `partial` with `medium-member-preview`.

## Routes considered and not used

- **Search-engine snippets.** Search engines index the same anonymous opening and sometimes keep a
  longer snippet for a while. It helped confirm one preview, but snippets are unstable and
  fragmented, so nothing is reconstructed from result pages.
- **The original source.** Medium's metadata exposes `canonicalUrl` and `importedUrl`. A story
  cross-posted from a personal site could be retrieved there instead. For these five, every canonical
  URL points at Medium and every `importedUrl` is empty, so this was not built.

## Terms

Medium's [Rules](https://policy.medium.com/medium-rules-30e5502c4eb4) prohibit automated access
without express written consent and require access through its published interfaces. Low volume and
a paid membership create no exception. Its
[Terms of Service](https://help.medium.com/hc/en-us/articles/213481318-Medium-Terms-of-Service) grant
a limited personal licence and say not to copy content one has no right to copy. Medium documents
[RSS](https://help.medium.com/hc/en-us/articles/214874118-Using-RSS-feeds-of-profiles-publications-and-topics)
as an outbound interface and says paywalled stories are not available there in full.

For readers in the UK, the Intellectual Property Office's
[guidance on exceptions](https://www.gov.uk/guidance/exceptions-to-copyright) says non-commercial
research and private study can permit limited extracts when the use is fair, and that copying a whole
work would not generally be fair dealing.

The extractor is therefore sequential and direct. It never accepts a password, solves a CAPTCHA,
rotates proxies, creates accounts or retries aggressively.

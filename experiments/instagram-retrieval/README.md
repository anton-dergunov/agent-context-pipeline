# Instagram retrieval without an account

**Question.** Every Instagram extraction from 30 October 2026 on came back empty. Is that Instagram
refusing this deployment, or refusing the client — and which client still retrieves a public post
without a login?

**Status.** Measured 20 Nov 2026 over two public posts. Instaloader replaced by yt-dlp in
[`docs/extractors/instagram.md`](../../docs/extractors/instagram.md#why-yt-dlp).

## The outage

Counted over the Instagram extractions still in the laptop inbox on 20 Nov 2026: 101 retrieved and
2 failed before 30 Oct; from that day, 86 refused and none retrieved. Every refusal was the same:

```text
ConnectionException: JSON Query to graphql/query: 401 Unauthorized -
"fail" status, message "Please wait a few minutes before you try again."
```

Constant for three weeks, so not a rate limit that lifts. It went unnoticed because each item still
arrived — with its caption from the link title and `extraction: failed` — and because the reason key
was `post-unavailable`, which reads as a deleted post rather than a refused client. Both posts
measured here opened in a private browser window from the same network, so the address was not
blocked.

## Method

`probe.py` asks four clients for the same posts, anonymously, each run out of process the way it
would be installed:

```bash
uv run python experiments/instagram-retrieval/probe.py SHORTCODE [SHORTCODE ...]
```

Post 1 is an 11-image carousel, post 2 a carousel of one image and five videos. The posts are
numbered in [`results.json`](results.json), not named: which posts were saved is private.

## Results

| Client | Post 1 | Post 2 |
| --- | --- | --- |
| Instaloader 4.15.3 (the pinned version) | 401 "Please wait a few minutes" | 401 |
| Instaloader 4.16 (latest, 26 Oct 2026) | `BadResponseException` | `BadResponseException` |
| gallery-dl 1.32.20 | redirected to login | redirected to login |
| yt-dlp (the project's pin, with curl-cffi) | 11 slides, 58-word caption, 12 comments | 6 slides, 50-word caption, 9 comments |

Through the rewritten extractor both posts come back `complete`: 11 and 6 media files, and with
on-screen text 602 and 206 words of `content.md` against 74 and 66 for the caption alone.

## Why yt-dlp gets through

Read from its Instagram extractor: it presents a browser's TLS fingerprint (curl-cffi
impersonation), fetches the page first for its `lsd` token, and asks
`PolarisLoggedOutDesktopWWWPostRootContentQuery` — the query Instagram's own logged-out web client
asks — falling back to the JSON embedded in the post's HTML. Instaloader asks a GraphQL `doc_id`
that Instagram has stopped answering for anonymous callers, from a plain `requests` client.

The fix that lasts is less which client than how it is updated: yt-dlp runs through the
self-updating runner YouTube already uses, so the next change on Instagram's side is picked up from
the nightly channel within a day, without a lockfile change.

## Not tried

A logged-in session in any client — the point was to need none — and paid scraping APIs, which would
send every saved post to a third party.

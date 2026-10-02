# Experiment · what does routing cost, and what does an item need to carry?

**Question.** Where does an agent filing saved links actually spend its budget, how much of that can
be done on the server beforehand, and how large may a per-item index be?

**Status.** Measured 14 Aug 2026, before the item contract was designed. The contract in
[`docs/architecture/item-contract.md`](../../docs/architecture/item-contract.md) was built from these
numbers. The token budget below is an estimate that has not been re-measured against real routing
sessions.

**Apparatus.** A write-up only. The script that parsed the session transcripts was not kept, and the
transcripts are private. The extractor outputs measured here were local runs over the fixture URL
lists in `tests/fixtures/`.

## Where routing spends its budget

90 transcripts of agent sessions in the owner's notes repository were parsed; 77 of them involve
routing. Tool calls across all sessions:

| Tool | Calls |
|---|---|
| Edit | 1,650 |
| Bash | 1,443 |
| Read | 765 |
| **WebSearch** | **385** |
| Write | 375 |
| **WebFetch** | **223** |

**Search outnumbers fetch 1.7 to 1.** The expensive part of routing is not downloading a page. It is
working out what a thing is and whether it is still current: 203 of the 385 queries contain the year.
The queries split into two kinds, and the distinction drives the design:

- **Identification** — which playlist is this, which article series, who wrote it and when. This is
  fully pre-computable and is pure waste at routing time. One playlist cost three consecutive
  searches.
- **Currency** — has this been superseded. This is not pre-computable: it needs the live web and a
  judgement. Pre-extraction does not remove it, but turns an exploratory search into one targeted
  search, because the agent starts with a title, an author and a date instead of a bare URL.

So enrichment removes the retrieval and identification cost, and sharpens but does not remove the
currency check.

**One fetch in ten fails outright.** 21 of 223 (9.4%) returned a hard error, dominated by HTTP 403,
with the rest spread over 404, 410, timeouts, unresolvable hosts and an expired certificate. The
failing sources are publishers, journals and course platforms, exactly the sources whose quality the
agent is supposed to judge. Across the corpus, `403`, `paywall` and `unverified` appear in 179, 102
and 76 transcript messages, and a link shortener in 52.

**The failures are nearly silent.** A 403 does not stop a session; it produces a finding marked
unverified, which is correct behaviour and a degraded decision. The contract therefore has to make
visible which findings rest on retrieved evidence, which is what the `extraction` field does.

## What the extractors already produce

The 97-URL corpus is its own experiment: [`url-extraction-corpus/`](../url-extraction-corpus/README.md).
89% of it is retrieved, and the remaining 11% is enumerated with stable reason keys.

**Body sizes** are the reason layering is mandatory:

| Artifact | Size | Tokens, roughly |
|---|---|---|
| an arXiv paper's abstract | 1.7 KB | 420 |
| the same paper's body | 80 KB, 11,931 words | 20,000 |
| a technical blog post | 10 KB, 1,458 words | 2,600 |
| a research PDF | 82 KB, 12,186 words | 20,500 |
| a 77-minute lecture's transcript | 68 KB | 17,000 |
| a 26-second Short | 1.7 KB | 430 |
| a Medium article | 9.7 KB, 1,544 words | 2,400 |
| five LinkedIn posts | 1.1–5.6 KB each | 270–1,400 |

Twenty items at full body would be 100,000 to 300,000 tokens. Twenty items at abstract plus metadata
is a single-digit number of thousands. There is no third option.

**Composition of an Instagram extraction**, over 11 posts:

| Part | Bytes | Share |
|---|---|---|
| caption | 9,743 | 13% |
| **comments** | **28,502** | **38%** |
| on-screen text | 31,416 | 42% |
| total | 74,660 | |

The comments are 38% of the payload and, for Instagram, almost pure noise: emoji and one-word
applause. The on-screen text is the content of a reel and has to stay. **Decision: Instagram
comments never reach the index.**

**LinkedIn is the opposite case.** In one post announcing a paper, the body is 1,700 characters of
readable commentary, the author's own first comment is the link to the paper, and the nine comments
that follow are other people's, several visibly generated filler. **Decision: the author's own
comments lead, and their links are followed; when the author left none, the first comment carrying
an outbound link.**

## What the inbox looked like before

39 items, with a 539-line generated digest. Defects the contract had to fix:

1. **Bodies were pasted whole into the digest.** One weekly newsletter occupied 47 lines of it.
2. **Segment heading levels were inconsistent** between items. The bug disappears once segments
   leave the index.
3. **Hyperlinks were silently lost.** Telegram's plain text drops the destination of hyperlinked
   text, so one item carried two `text_link` entities that appeared nowhere in its body, and another
   showed four link labels with no links. Link discovery therefore reads `entities` and
   `caption_entities` and does not rely on scanning the text.
4. **Forwarding provenance was captured and unused.** `forward_origin` names the channel a post came
   from, a free source-quality signal: an aggregator channel is a secondary source.
5. **URL resolution already worked.** An item with four shortened links arrived with four real
   destinations, the highest-value thing shipped at the time.

## The token budget

Per-item index cost, estimated at about four characters per token from the artifacts above:

| Item type | Frontmatter | Captured | Lead | Sources and links | **Total** |
|---|---|---|---|---|---|
| voice note or plain text | 40 | 40 | — | — | **~80** |
| single web page | 90 | 20 | 160 | 40 | **~310** |
| research paper | 110 | 25 | 420 | 60 | **~615** |
| long YouTube video | 100 | 25 | 160 | 50 | **~335** |
| Instagram reel | 90 | 25 | 140 | 30 | **~285** |
| LinkedIn post leading to a paper | 110 | 25 | 420 | 90 | **~645** |
| 43-link list | 90 | 30 | — | 620 | **~740** |

A realistic 20-item day — six notes, five web pages, three papers, three videos, two social posts
and one link list — comes to about **6,100 tokens** for the whole digest.

| Approach | Cost for 20 items |
|---|---|
| bare URLs and live retrieval | ~400 tokens of text, then 20–40 fetch and search round trips at 1.5–3k each: **30–60k**, about a tenth of them returning nothing |
| full bodies inline | **100k–300k** |
| **an index per item** | **~6k**, plus the bodies the agent chooses to open |

The chosen drill-downs are the point: three items that need their bodies cost about 15,000 tokens
spent deliberately, on content already on disk, with no refused requests.

These figures predate short-form media being quoted stream by stream (up to 800 words), so a reel's
index is now larger than the row above.

## Not done

- The budget has not been compared with measured sessions on the finished contract.
- The 9.4% fetch failure rate was not re-measured after extraction moved to the server.

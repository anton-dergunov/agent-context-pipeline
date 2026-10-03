# Experiment · what does social-media formatting cost an agent?

**Question.** Posts copied from LinkedIn and Instagram arrive with bold and italic letters built
from mathematical Unicode, emoji bullets, hashtag lines and invisible characters. What does that cost
an agent reading them, and what does cleaning recover?

**Status.** Counted 3 Oct 2026 over the owner's old capture backlog, a 4,017-line Org file of pasted
posts as it stood on 1 Aug 2026, before this pipeline existed: **the 47 lines written in styled
letters cost 3,401 tokens, and 699 once normalized — 4.9 times as many.** Cleaning the whole file
saves 8.5% of its tokens (53,952 → 49,364).

**Serves.** [`docs/architecture/preprocessing.md`](../../docs/architecture/preprocessing.md#text-cleaning).

## Method

- **Corpus.** The backlog an agent was filing by hand before the pipeline: 4,017 lines, 596 URLs (221
  of them `lnkd.in`), 250 lines with emoji and 47 with styled letters. It is private and not
  committed.
- **Apparatus.** [`tokens.py`](tokens.py) counts tokens line by line, raw and after the pipeline's own
  `clean_line`, and separately for the styled lines with Unicode normalization alone, so that their
  number is not mixed with removed emoji and hashtags. It prints aggregates only;
  [`result.json`](result.json) is its output.

  ```bash
  uv run --with tiktoken python experiments/text-cleaning/tokens.py <file>
  ```

- **Tokenizer.** tiktoken's `o200k_base` stands in for a model's tokenizer. No Claude tokenizer is
  public; the ratios, not the absolute counts, are the finding.

## Results

| | Raw | Cleaned |
|---|---|---|
| 47 lines in styled letters | 3,401 tokens | 699 tokens (normalization only) |
| the whole file | 53,952 tokens | 49,364 tokens |
| "𝗣𝗼𝘁𝗲𝗻𝘁𝗶𝗮𝗹 𝗽𝗮𝗿𝗮𝗱𝗶𝗴𝗺 𝘀𝗵𝗶𝗳𝘁" | 50 tokens | 3 tokens ("Potential paradigm shift") |

Tokens are the measurable part. The other costs are matching rather than reading:

- **A search for the word misses it.** `Agents` does not match `𝐀𝐠𝐞𝐧𝐭𝐬`; neither `rg` nor a keyword
  index sees them as the same word, and the agent was filing these posts by searching the notes.
- **An invisible character breaks a link.** A zero-width space inside a URL stays inside it: the
  link is found, but it is now a different address that leads nowhere, and nothing on screen shows
  why. This is why cleaning runs before link discovery.

Whether such characters also hurt a large model's comprehension is a weaker claim. Boucher et al.,
[*Bad Characters: Imperceptible NLP Attacks*](https://www.research.ed.ac.uk/en/publications/bad-characters-imperceptible-nlp-attacks/)
(IEEE S&P 2022), show that a few invisible characters, homoglyphs or reorderings can break deployed
NLP models. Later studies find large LLMs fairly robust to character-level noise when reading. The
pipeline cleans for tokens and matching, which are measured here, not for comprehension.

## Limits

- One file, one tokenizer.
- Cleaning also removes hashtag lines and decorative emoji, so the whole-file saving mixes removal
  with normalization. The styled-line figure is normalization alone.

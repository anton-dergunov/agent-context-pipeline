# Agent context pipeline

A self-hosted pipeline between what you come across during the day and the coding agent that files it. Saving something takes one share from the phone or one shortcut in the browser. By the time you sit down to review, each item has been cleaned, its links followed to where they lead, the article, paper or video behind it retrieved as Markdown, and the text inside its images and video read. The agent works from local files instead of fetching, guessing and searching.

It is *context engineering* for a personal inbox: deciding what reaches the agent, in what shape, and measuring what that saves. Every step runs on your own server, with no LLM and no hosted AI service, and every measurement on this page comes from an [experiment](experiments/README.md) on real captures.

![One item's way through the pipeline: a LinkedIn post is captured from the browser with a selected quote and an intent; the server cleans it, finds and resolves its link, extracts the post and follows the author's comment to the arXiv paper it discusses, then renders index.md; ./sync.sh brings it to the laptop and adds possible neighbours from the user's own notes; the item appears as number 7 in triage.org, and the agent is asked to file item 7.](assets/pictures/overview.png)

## What it does

- **Capture instead of forgetting.** One share to a Telegram bot from the phone or tablet, one shortcut in Chrome, or one command. It replaces pasting links into a notes file by hand, which on a tablet took many steps and mostly did not happen. [Capturing](#capturing)
- **Reads the text inside images and video.** On-screen text is recognized frame by frame and speech is transcribed (currently in English, Spanish, Russian and Mandarin). In experimental measurements in 30 of 52 saved Instagram posts, the images and video said more than the caption. [More](#text-inside-images-and-video)
- **Opens the links an agent cannot.** Short links are followed to their destination and given the page's own title (in experiments 192 of 193 LinkedIn short links from old notes now reach the page they point to). Styled letters, emoji bullets and invisible characters are cleaned out; lines in styled letters cost 4.9 times the tokens of plain ones. [More](#links-an-agent-can-open)
- **Follows a post to what it is about.** When the paper is in the author's comment or a video's description, the paper or repository is retrieved and becomes the item. A post with 43 links arrives as 43 titles, about 600 tokens instead of 107,000. [More](#from-the-post-to-its-source)
- **Papers in one shape.** arXiv, ACL Anthology, PMLR, OpenReview, CVF, NeurIPS and JMLR papers all become the same Markdown, with the complete abstract up front and the PDF kept.
- **Everything already on disk, with its size.** Each item's `index.md` says what was captured, what was retrieved and how many words each body is, so the agent opens a body only when it is worth it.
- **Points to where an item probably belongs.** Each item can arrive with the two nearest passages from your own plans and notes; about 88% of the rows shown are right. Given them, an agent routed the same items with 30–40% fewer tokens and no exploratory searches. [More](#where-it-probably-belongs)
- **A queue for you as well as for the agent.** Both read the same numbered queue: the agent as one Markdown file, you as a list in your editor, with one key per decision in Emacs. [Reviewing](#reviewing)
- **Nothing captured is lost.** Everything after capture is enrichment. A step that fails is recorded on the item, and the item still arrives.

Capturing is immediate and deciding is deferred: what to do with an item is settled later, in a review session with the agent, not at the moment of saving it.

The repository is `agent-context-pipeline`; the program inside it is called `info-triage`, which is the name its commands, settings and folders carry.

## Why it exists

It began as a notes repository of 45 Org files and 12,449 lines, the worst of them a 4,017-line pile of pasted posts, and a coding agent asked to file it all. A person reading that pile clicks a short link and moves on. For the agent each one was a dead end. It wrote "`lnkd.in` shorteners cannot be resolved" into its own instructions, identified a paper behind a login wall by decoding the post's URL, and marked Russian books down because, in its words, "I can verify an English resource with one search where a Russian one often needs three." Across 90 sessions it ran 385 web searches against 223 page fetches, and one fetch in ten failed outright, mostly without anyone noticing.

Each of those is a retrieval problem, and none of them needs a language model to solve. This pipeline solves them before the agent starts.

![Before and after. Left: an excerpt of the old Unsorted.org backlog, a list of agent resources whose every link is an lnkd.in short link; 596 links in the file, 221 of them lnkd.in. Middle: three quotes from the agent while filing it: that lnkd.in shorteners cannot be resolved, that a paper behind a login wall was identified from the post's URL, and that it can verify an English resource with one search where a Russian one needs three. Right: the same five links as index.md now lists them, each with its real destination and title, such as ReAct on arXiv. Along the bottom: 385 web searches against 223 fetches in 90 sessions, one fetch in ten failed, 192 of 193 LinkedIn short links now resolved, and about 6k tokens for a 20-item day against 30–60k for bare links.](assets/pictures/before-after.png)

## How it works

```text
a Telegram bot, the browser extension, or POST /capture
    -> nearby Telegram messages are grouped into one item
    -> saved durably on the server
    -> that route's steps run: transcribe, clean, find links, resolve, extract, render index.md
    -> server inbox:   data/inbox/<route>/<YYYY-MM-DD>_<n>/
    -> ./sync.sh
    -> laptop inbox:   ~/info-triage-inbox/<route>/  with triage.md and triage.org
    -> review with the agent; removing an item's directory marks it processed
```

The server is one small container on any machine that runs Docker and stays on: a home server, a NAS, a rented VPS. It can also be the laptop itself, in which case nothing is copied anywhere and the same commands work.

## What arrives in the inbox

Every item is a directory with one `index.md` at its root: what was captured, the sources retrieved for it with their word counts, a short lead, its links, and what went wrong if anything did. The bodies sit beside it as Markdown. [`docs/architecture/item-contract.md`](docs/architecture/item-contract.md) specifies the file, and [`docs/architecture/preprocessing.md`](docs/architecture/preprocessing.md) every step that writes it.

### Text inside images and video

Short videos and carousels often put the content in the picture and use the caption as an advert, sometimes one that asks for a comment to get the list it is withholding. The server samples three frames a second, recognizes the text in each, merges what repeats across frames, and transcribes the speech; music-only video yields no invented words. In the item's lead the on-screen text and speech come first and the caption last.

![A reel whose 21-word caption says to comment "hired" to get the full list of projects, beside six frames sampled at three a second and the on-screen text recognized from them: all twelve project names, from LLM Output Arbitration System to Automated Eval Dataset Generator from Production Logs. Beside it, a Russian carousel titled "10 places off the usual routes of Guangzhou" whose place names, such as Dongshankou, Kuiyuan 1922 and Shenjing Ancient Village, appear only in the images. Along the bottom: 30 of 52 saved posts held more words in images and video than in the caption; 89% of on-screen text kept at 11% of the frames; about 50 seconds per minute of video on an M1 MacBook Air; speech in four languages.](assets/pictures/video-text.png)

Reading 3 frames a second instead of every frame keeps 89% of the substantial text for 11% of the work, about 50 seconds per minute of video with the server's engine on an M1 MacBook Air. [`experiments/video-ocr/`](experiments/video-ocr/README.md) has the measurements, [`experiments/transcription-models/`](experiments/transcription-models/README.md) the choice of speech model per machine, and [`experiments/instagram-streams/`](experiments/instagram-streams/README.md) how much of a saved post lives outside its caption. The extractors are described in [`docs/extractors/instagram.md`](docs/extractors/instagram.md) and [`docs/extractors/youtube.md`](docs/extractors/youtube.md).

### Links an agent can open

A post copied from LinkedIn carries no destinations at all: every link is a `lnkd.in` short link, which a person clicks through and an agent cannot open. The pipeline follows each short link to its real destination, drops tracking parameters, and rewrites the link with the page's own title, so the agent sees what a link is before deciding to open it. Cleaning runs first, so that styled letters and invisible characters cannot hide a word from a search or break a link.

![A post pasted from LinkedIn, 185 tokens: a rocket emoji, a headline in mathematical bold letters, keycap-numbered bullets, three lnkd.in links, one with a zero-width space inside it, and a line of hashtags. Three steps, text cleaning, link discovery and URL resolution, turn it into a 58-token plain body whose links carry their titles and real addresses, such as ReAct: Synergizing Reasoning and Acting in Language Models at arxiv.org/abs/2210.03629. Along the bottom: 192 of 193 LinkedIn short links resolved; 92% of 529 bare links titled; styled letters cost 4.9 times the tokens; 97 shortener services recognized.](assets/pictures/links-and-text.png)

[`experiments/url-title-audit/`](experiments/url-title-audit/README.md) measures the titles over 529 real links, and [`experiments/text-cleaning/`](experiments/text-cleaning/README.md) what the formatting costs.

### From the post to its source

The interesting part of a post is usually what it points at. When a LinkedIn author puts the paper in their own comment, or a YouTube channel puts it in the description, that link is followed once and the paper or repository is retrieved: it becomes the item, and its abstract becomes the lead. A followed link is never followed further. A post with many links is read as a list of titles instead, since forty bodies would cost a fortune and change nothing about where the list belongs.

![Three real cases. A LinkedIn post about stealing reasoning traces leads, through the author's comment, to the arXiv paper, 6,384 words with its PDF; the item becomes kind paper with the abstract as its lead. A LinkedIn post announcing an NVIDIA reinforcement learning framework leads, through the author's comment, to the GitHub repository, 4,155 words. A post with 43 links becomes a link list of 43 resolved titles with two bodies extracted, about 600 tokens. Below: papers from arXiv, ACL Anthology, PMLR, CVF, NeurIPS, JMLR, OpenReview and ResearchGate share one layout, content.md with title, authors, date, abstract and body, and the PDF kept beside it.](assets/pictures/follow-to-source.png)

Of 97 real saved links, 86 were retrieved and converted, every arXiv paper among them; each failure carries a stable reason ([`experiments/url-extraction-corpus/`](experiments/url-extraction-corpus/README.md)). Retrieved bodies are cached, so editing a message never downloads a paper twice.

### Where it probably belongs

Filing an item means finding the plan or note it belongs with. An agent does that by inventing search terms and reading around every hit. After each sync the pipeline does it once, by retrieval over your own Org plans and Markdown notes, and adds the two nearest passages to each item's index as unverified candidates. The wording matters: stated as fact, the same rows made the agent merge every item; hedged, it judged each one.

![Without candidates, routing six real items took 17 repository-wide searches, 17 narrowed ones and three reads of one plan file; with them, no exploratory search, only the cited lines. The middle shows item 7's possible neighbours: a plan task about distilling reasoning traces from a large model into a small one, and a weaker vault match the wording flags. On the right, tokens to route the same six items: about a million with no field or with the rows stated as fact, which merged all six; 843k hedged; 697k with the likely destination file; 601k with the stronger model. Along the bottom: about 88% of rows correct; the right destination file 18 times in 20; about a minute for 92 items; no LLM.](assets/pictures/neighbours.png)

The rows are for you as much as for the agent: they are in the same index you read. [`docs/architecture/related-notes.md`](docs/architecture/related-notes.md) describes the pass, and [`experiments/related-notes/`](experiments/related-notes/README.md) its measurements, one run per condition.

## Install

You need [Docker](https://docs.docker.com/get-docker/) where the server runs, and [uv](https://docs.astral.sh/uv/) on the machine you review from. The full walkthrough is [`docs/operations/setup.md`](docs/operations/setup.md).

### 1. Start the server

```bash
git clone https://github.com/anton-dergunov/agent-context-pipeline.git
cd agent-context-pipeline
cp .env.example .env                  # set INFO_TRIAGE_CAPTURE_TOKEN: openssl rand -base64 32
cp config.example.yaml config.yaml    # the routes and their processing; fine as it is
./run.sh                              # docker compose up --build; the first build downloads the models
```

`http://localhost:8000/` is the dashboard: what has arrived, what is processing and what each step made of it. No Telegram setup is needed for this first run; the browser extension and the command line can already capture.

To run it on another machine, name that machine in `.env` and deploy from here:

```dotenv
INFO_TRIAGE_SERVER=my-server                # an alias from ~/.ssh/config, or user@host
INFO_TRIAGE_SERVER_DIR=/srv/info-triage     # the project directory there
```

```bash
./deploy.sh        # copy the project over SSH, rebuild, restart, wait for the health check
```

[`docs/operations/deployment.md`](docs/operations/deployment.md) covers what the server needs, and [`docs/operations/synology.md`](docs/operations/synology.md) the extra steps on a Synology NAS.

### 2. Add Telegram bots (optional)

Sharing to a bot is the one-tap path from a phone. Create a bot per route with [@BotFather](https://t.me/BotFather), put each token in `.env`, and uncomment that route's `token_env` line in `config.yaml`. [`docs/operations/setup.md`](docs/operations/setup.md#5-telegram-bots) has the steps, and [`assets/bot-icons/`](assets/bot-icons/) a set of profile pictures that tell the bots apart in the share sheet.

### 3. Install the browser extension

Download the zip from [Releases](https://github.com/anton-dergunov/agent-context-pipeline/releases), unpack it, and load the folder at `chrome://extensions` with Developer mode on. Enter the server URL and the capture token on its options page. [`extension/README.md`](extension/README.md) has the details.

### 4. Bring the inbox to your laptop

```bash
uv sync
./sync.sh
```

This fills `~/info-triage-inbox/` from the server named in `.env`, or from this checkout's own `data/` when none is named. Run it whenever you want to review.

## Capturing

Every client sends the same thing to the same endpoint, so the way in is whatever is at hand: the share sheet on a phone or tablet, a shortcut in the browser, a command, a script.

![Capture or forget. Before: saving one link took seven steps on a tablet, from copying the address and opening Inbox.org to finding the article again. Now: one share to a Telegram bot per route (info, job, lang, clip), the Chrome dialog with a route, the page address, the selected text and an intent, or the command line and a plain HTTP call, all into POST /capture. On the right, the routes in config.yaml, labelled as yours to define: info runs all six steps, job only finds links and renders the index. Along the bottom: one share instead of seven steps; Telegram messages within three seconds become one item; a failed step never loses the item; no bot is needed to start.](assets/pictures/capture.png)

### From Telegram

Share or forward anything to one of the bots: text, links, forwarded posts, documents, photos, video, voice notes, locations. Messages sent within three seconds of each other become one item, so a link followed by a typed comment arrives together. Editing a message later updates the same item. The bot stays silent unless a capture fails.

### From the browser

Press `Ctrl+Shift+K` (`Command+Shift+K` on a Mac) on any page. The dialog carries the page address and any selected text; pick a route, add a note, send. It stays open afterwards, and sending again updates the same item.

### From the command line or a script

`POST /capture` takes the same item from anywhere, and `info-triage-capture` is its client:

```bash
uv run info-triage-capture "https://example.com/article  worth a look"
uv run info-triage-capture --route job "https://example.com/posting"
echo "a thought to keep" | uv run info-triage-capture
uv run info-triage-capture --file spec.pdf "the spec I mentioned"
```

It reads the token from `.env`, and the server address from `INFO_TRIAGE_CAPTURE_URL` there (`http://localhost:8000` when unset). Each capture prints the handle of the item it created, such as `job/2026-08-18_4`. Passing that handle back rewrites the item instead of capturing a second one:

```bash
uv run info-triage-capture --id job/2026-08-18_4 "the posting, with the note I meant"
uv run info-triage-capture --id job/2026-08-18_4 --route info "actually just worth reading"
```

A rewrite replaces the whole item: attachments not attached again are dropped, and the route's processing runs from scratch. Re-filing it under another route renumbers it, so the printed handle is the one to keep. The capture time cannot be changed, and an item captured from Telegram is edited by editing the Telegram message.

The endpoint requires the bearer token and is otherwise reachable from the whole local network, as the dashboard is. Reaching it from elsewhere is a job for a private network such as Tailscale: [`docs/operations/tailscale-https.md`](docs/operations/tailscale-https.md).

## Routes

Routes are configuration: a route is a list of processing steps, its own inbox and queue, and usually its own bot, so choosing a route costs nothing beyond choosing whom to share to. Define as many as you like in `config.yaml`, under any names, each running the steps it needs; the first is the default. The template's three are examples to start from, not a scheme to follow:

| Example route | For | Automatic processing |
|---|---|---|
| `info` | Things to think about and file later | Everything: transcription, cleaning, link resolution, retrieval, index |
| `job` | Job postings | Records the link. Nothing is fetched or rewritten |
| `clip` | Video clips to download later | Records the link. Downloading happens on the laptop |

Shared to the wrong bot? Edit the message and add the route's name as a hashtag, such as `#job`. The item moves to that route, is renumbered there, and re-runs that route's processing. Two route hashtags at once change nothing.

## Reviewing

Each sync leaves, in every route's directory, the item directories and two generated views of them, numbered the same way:

- `triage.md` is the one the agent reads: the items' `index.md` files in order, one `### N — <id>` section each.
- `triage.org` is a compact list for a person, one or two lines per item.

Point your agent at `~/info-triage-inbox/info/triage.md` and pick items by number ("file items 1, 5 and 10"). Moving or deleting an item's directory is the only way to mark it processed; the next sync removes it from the server. If the original message is edited later, the item comes back.

No particular editor is required. [`docs/reviewing.md`](docs/reviewing.md) describes the workflow. In Emacs, [agentic-org-planner](https://github.com/anton-dergunov/agentic-org-planner) turns the queue into a reading list with one key per decision, and opens every item and extracted body beside it, so the queue is as workable by hand as by the agent.

![The queue in Emacs: triage.org lists the day's items by number with their kinds, item 29 highlighted; below it, that item's index.md, a Russian Instagram carousel about Guangzhou, with its sources, word count and the on-screen text as its lead. Beside it, the keys: n and p to move, RET to open beside, d to drop the item, which marks it processed, s to sync, g to renumber, e to open the folder, and back and forward. triage.org is for you and triage.md for the agent, with the same numbers.](assets/pictures/emacs.png)

## Documentation

- [`docs/operations/setup.md`](docs/operations/setup.md) — installing, the settings in `.env` and `config.yaml`, defining routes.
- [`docs/`](docs/README.md) — architecture, the item contract, the preprocessing catalogue, the extractors, deployment.
- [`experiments/`](experiments/README.md) — the measurements behind the design and behind every number on this page, with their apparatus and results.
- [`assets/pictures/`](assets/pictures/) — the HTML sources of the pictures on this page, and `render.sh` to redraw them.

## License and credits

[MIT](LICENSE). The bot and extension icons are from [Flaticon](https://www.flaticon.com/) and keep their own licence: [Reading](https://www.flaticon.com/free-icons/reading), [Languages](https://www.flaticon.com/free-icons/languages) and [Video](https://www.flaticon.com/free-icons/video) icons created by Magnific, and [Job](https://www.flaticon.com/free-icons/job) icons created by surang.

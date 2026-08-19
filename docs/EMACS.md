# Reviewing the inbox from Emacs

`sync.sh` writes two views of the same items, both generated and both
overwritten on every sync:

- **`triage.md`** — the whole contract, one `### N — <id>` section per item
  under a `## <date>` heading per day. This is what `/route` reads.
- **`triage.org`** — navigation: a heading per day, and under it one heading per
  item carrying the number and a one-line label that is itself the link to the
  item's index, with the kind as a trailing tag, the user's own note when there
  is one, and links to the directory and the source. No extracted text reaches
  it beyond that label, which is the whole reason the items themselves stay
  Markdown (`Message-contract-design.md` §4.4): Org gives `*`, `_` and `[[`
  structural meaning, and an escaper bug would corrupt items rather than merely
  look wrong.

**Saving one link twice.** It happens — read a page, get distracted, save it
again. The two captures stay two items, keep two numbers and are never deleted,
but the later one is marked in both views. In `triage.org` it takes a second tag
and a trailing note on its link line:

```org
** 6 · [[file:2026-08-18_6/index.md][Just saw that the repository passed 100,000 stars]]  :post:dup:
   [[file:2026-08-18_6/][directory]] · [[https://www.linkedin.com/posts/…][source]] · dup of 1
```

In `triage.md` its section keeps its frontmatter and what it captured — the note
you wrote the second time is not the one you wrote the first — and says where the
rest is, rather than repeating a body that is identical by construction:

```markdown
**Duplicate capture** — the same link as item 1 (`2026-08-18_1`). Its sources,
lead and links are printed there and are not repeated here; what differs is
below. Route it once, then drop both directories.
```

Route it once and drop both directories: the numbering makes them two items, but
they are one thing.

**The two numberings are the same numbering.** Both views are rendered from the
same oldest-first list in the same pass, and that is the entire interface
between the halves: the user picks numbers out of the Org view and quotes them
into a routing request that reads the Markdown one. Anything that changes what
is in the inbox must rebuild both — `sync.sh --regenerate` does exactly that and
nothing else.

Both group by day, and the numbering runs straight through the days rather than
restarting under each one. Grouping is the same view for a person and for an
agent; a per-day numbering would be three items called 1.

Neither file carries state. **Deleting an item's directory is the signal that it
was processed**; a mark written into a generated file would be lost on the next
sync.

## `triage.org` is not for agents

It opens by saying so:

```org
#+AGENT_NOTE: Reading this as an agent? Stop — read triage.md in this directory instead.
#+AGENT_NOTE: This is a navigation view for a person and holds strictly less than triage.md.
```

Keyword lines rather than `#` comments, and not only for tidiness: Org gives an
unknown keyword the same faded `org-meta-line` treatment as `#+TITLE:` and hides
the `#+` itself, so the notice reads as metadata. As a plain comment it took
ordinary body styling and ended up louder on screen than the title above it.

An editor integration advertises whatever file is on screen, and this one is on
screen for the whole of a triage session. Reading it costs tokens and can only
produce a worse answer than reading `triage.md`, which holds everything it holds
and more. `/route` states the substitution as a rule, so naming `triage.org`,
naming "triage", or invoking `/route` with the queue on screen all resolve to
`triage.md`.

## The split

Emacs owns the loop end to end: sync, read the queue, open an item's artifacts,
drop what is not worth keeping. It does not try to be a media viewer — a PDF is
handed to Preview and a video to the system player — but browsing an item, and
reading everything textual in it, happens without leaving the editor. VS Code
remains the escape hatch for an item whose payload is twenty photos, one
keystroke away.

The intended shape is two windows side by side: `triage.org` in Emacs to work
the queue, and a Claude Code session beside it to drive `/route`.

## Setup

The Emacs half lives in the user's own configuration rather than here, because
it needs that configuration's window management, and because half of it is
generally useful. In the `productivity-system` repository, see
`docs/Info-triage.org`, `lisp/ps-info-triage.el`, `lisp/ps-open.el` and
`lisp/ps-nav.el`. Two settings point it here:

```elisp
(setq ps/info-triage-directory   "~/info-triage-inbox/info/"
      ps/info-triage-sync-script "~/projects/tools/info-triage/sync.sh")
```

It points at **one route's** directory, not at the inbox root: each route holds
its own items with its own `triage.org` beside them, and only `info` is triaged
by hand — `job`, `clip` and `lang` are consumed by other scripts. Point the
setting at another route's directory to work that one instead; nothing else
changes, because each view sits next to the items it lists and so every path
inside it stays a single segment.

Everything is hidden when that directory does not exist, so the configuration
stays shareable with people who do not run this project.

The older standalone `emacs/info-triage.el` in this repository predates that and
is kept only for someone who wants the four commands without the rest.

All of these are on the editor's Productivity → Triage menu as well.

| Command | Does |
|---|---|
| `ps/info-triage-open` (`C-c p I`) | opens the queue |
| `ps/info-triage-sync` | runs `sync.sh` in the background, then refreshes the queue |
| `ps/info-triage-regenerate` | `sync.sh --regenerate` — renumbers both views, no network |
| `ps/info-triage-drop` (`d`) | moves the item at point's directory to the Trash, then renumbers |
| `ps/info-triage-open-externally` (`e`) | opens the item's directory in VS Code |

## Working the queue

The buffer is read-only — it is regenerated wholesale on every sync — which
frees single keys: `RET` follows, `n`/`p` move between items, `d` drops, `s`
syncs, `g` renumbers, `e` opens the folder externally, `b`/`f` go back and
forward.

Where a followed link opens depends on what else is on screen: beside the queue
when the queue is the only thing open, and in the queue's own window when it is
not — so reviewing alone gives two panes, and reviewing next to a Claude Code
session does not try for three. `‹ ›` on the mode line walks the trail back out
again, and never adds a window.

What a click does depends on the file: Markdown and JSON render in Emacs, HTML
renders as a page rather than as source, images open inline, PDFs and video go
to the desktop, audio is declined because the transcript beside it is the
readable copy.

`d` deletes to the Trash rather than unlinking, because the decision is made at
a glance and a glance is sometimes wrong. The next `sync.sh` propagates the
removal to the NAS either way.

## The minute after a sync

`sync.sh` prints `Synchronization complete — the inbox is ready to review now`
and then keeps running for about a minute, counting through the items. That
second phase is looking for each `info` item's possible neighbours in the plans
and the vault, and it writes a section only `/route` cares about. **Start
reading as soon as the ready line appears** — the items and both views are
complete at that moment, and the pass is deliberately after them for exactly
this reason.

Working the queue while it runs is safe, including dropping items. Every index
it needs is read before the search starts, and nothing is written to an item
that has gone: a dropped item is reported as `Item is no longer here, leaving
it` and the run carries on. When it finishes it rewrites `triage.md` alone, so
the `triage.org` buffer in front of you never changes under you.

If the pass cannot run — no notes directory, or the optional dependency missing
— it says so in one line and the sync is unaffected. `sync.sh --no-neighbours`
skips it outright, `g` (`--regenerate`) reuses the sections already in the item
directories, and `sync.sh --regenerate --neighbours` recomputes them after the
plans have moved.

## The item number

The number in a heading is what you pass to `/route` ("route items 1, 5 and
10"). It is positional and regenerated on every rebuild. The directory name is
what does not change — so a routing decision recorded against `5 —
2026-08-14_150` still identifies the right item after the next sync renumbers
it, which is why `/route` is told to quote both.

Emacs reads the directory name back out of the `[[file:<id>/][directory]]` link,
since that is the only place it appears now that the property drawer is gone.
It is one path segment and stays one: the views live inside the route directory
rather than above it, which is exactly what spared this regex the route.
That link's shape is a contract between the two repositories; `render_org` in
`info_triage/sync.py` says so, and so does `ps/info-triage--item-directory`. The
outline levels are part of the same contract: Emacs finds items by `^\*\* <N> `
and bounds one item's links by the next heading of any level, which for the last
item of a day is the day after it.

## In VS Code

Every relative link in `triage.md` resolves from the inbox root, so preview
navigation works. One setting makes it stay in preview rather than jumping to
source:

```json
"markdown.preview.openMarkdownLinks": "inPreview"
```

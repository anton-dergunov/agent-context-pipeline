# Reviewing the inbox from Emacs

`sync.sh` writes two views of the same items, both generated and both
overwritten on every sync:

- **`triage.md`** — the whole contract, one `## N — <id>` section per item. This
  is what `/route` reads.
- **`triage.org`** — navigation: one heading per item carrying the number, the
  date, the kind and a one-line label, the user's own note when there is one,
  and links to the index, the directory and the source. No extracted text
  reaches it beyond that label, which is the whole reason the items themselves
  stay Markdown (`Message-contract-design.md` §4.4): Org gives `*`, `_` and `[[`
  structural meaning, and an escaper bug would corrupt items rather than merely
  look wrong.

**The two numberings are the same numbering.** Both views are rendered from the
same oldest-first list in the same pass, and that is the entire interface
between the halves: the user picks numbers out of the Org view and quotes them
into a routing request that reads the Markdown one. Anything that changes what
is in the inbox must rebuild both — `sync.sh --regenerate` does exactly that and
nothing else.

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
(setq ps/info-triage-directory   "~/info-triage-inbox/"
      ps/info-triage-sync-script "~/projects/tools/info-triage/sync.sh")
```

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

## The item number

The number in a heading is what you pass to `/route` ("route items 1, 5 and
10"). It is positional and regenerated on every rebuild. The directory name is
what does not change — so a routing decision recorded against `5 —
2026-08-14_150` still identifies the right item after the next sync renumbers
it, which is why `/route` is told to quote both.

Emacs reads the directory name back out of the `[[file:<id>/][directory]]` link,
since that is the only place it appears now that the property drawer is gone.
That link's shape is a contract between the two repositories; `render_org` in
`info_triage/sync.py` says so, and so does `ps/info-triage--item-directory`.

## In VS Code

Every relative link in `triage.md` resolves from the inbox root, so preview
navigation works. One setting makes it stay in preview rather than jumping to
source:

```json
"markdown.preview.openMarkdownLinks": "inPreview"
```

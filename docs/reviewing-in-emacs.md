# Reviewing the inbox from Emacs

`sync.sh` writes two generated views of each route's queue: `triage.md`, which the agent reads, and
`triage.org`, a navigation view for a person. Their shape, shared numbering and the handling of
repeat captures are specified in [`architecture/sync.md`](architecture/sync.md). This page covers
working the queue from Emacs.

## The split

Emacs owns the loop end to end: sync, read the queue, open an item's artifacts, drop what is not
worth keeping. It does not try to be a media viewer — a PDF is handed to Preview and a video to the
system player — but browsing an item, and reading everything textual in it, happens without leaving
the editor. VS Code remains the escape hatch for an item whose payload is twenty photos, one
keystroke away.

The intended shape is two windows side by side: `triage.org` in Emacs to work the queue, and a
Claude Code session beside it to file items.

`triage.org` is not for the agent. It opens with a note saying so, because an editor integration
advertises whatever file is on screen, and this one is on screen for the whole session while holding
strictly less than `triage.md`. The routing skill states the substitution as a rule, so naming
`triage.org`, naming "triage", or starting a routing request with the queue on screen all resolve to
`triage.md`.

## Setup

The Emacs half lives in the owner's own configuration and not here, because it needs that
configuration's window management, and because half of it is generally useful. In the
`agentic-org-planner` repository, see `docs/Info-triage.org`, `lisp/ps-info-triage.el`,
`lisp/ps-open.el` and `lisp/ps-nav.el`. Two settings point it here:

```elisp
(setq ps/info-triage-directory   "~/info-triage-inbox/info/"
      ps/info-triage-sync-script "~/path/to/agent-context-pipeline/sync.sh")
```

It points at **one route's** directory, not at the inbox root: each route holds its own items with
its own `triage.org` beside them, and only `info` is reviewed by hand. Point the setting at another
route's directory to work that one instead; nothing else changes.

Everything is hidden when that directory does not exist, so the configuration stays shareable with
people who do not run this project.

The older standalone [`emacs/info-triage.el`](../emacs/info-triage.el) in this repository predates
that and is kept only for someone who wants the four commands without the rest.

| Command | Does |
|---|---|
| `ps/info-triage-open` (`C-c p I`) | opens the queue |
| `ps/info-triage-sync` | runs `sync.sh` in the background, then refreshes the queue |
| `ps/info-triage-regenerate` | `sync.sh --regenerate`: renumbers both views, no network |
| `ps/info-triage-drop` (`d`) | moves the item at point's directory to the Trash, then renumbers |
| `ps/info-triage-open-externally` (`e`) | opens the item's directory in VS Code |

All of these are on the editor's Productivity → Triage menu as well.

## Working the queue

The buffer is read-only — it is regenerated wholesale on every sync — which frees single keys: `RET`
follows, `n`/`p` move between items, `d` drops, `s` syncs, `g` renumbers, `e` opens the folder
externally, `b`/`f` go back and forward.

Where a followed link opens depends on what else is on screen: beside the queue when the queue is
the only thing open, and in the queue's own window when it is not. Reviewing alone gives two panes,
and reviewing next to a Claude Code session does not try for three. `‹ ›` on the mode line walks the
trail back out again, and never adds a window.

What a click does depends on the file: Markdown and JSON render in Emacs, HTML renders as a page and
not as source, images open inline, PDFs and video go to the desktop, and audio is declined because
the transcript beside it is the readable copy.

`d` deletes to the Trash instead of unlinking, because the decision is made at a glance and a glance
is sometimes wrong. The next `sync.sh` propagates the removal to the server either way.

A repeat capture of a link carries a `dup` tag and `dup of N`. File it once, then drop both
directories.

## The item number

The number in a heading is what is passed to the agent ("route items 1, 5 and 10"). It is positional
and regenerated on every rebuild, and `g` renumbers both views together after a drop. The directory
name is what does not change, which is why a routing decision is recorded against both.

Emacs reads the item's directory back out of the `[[file:<id>/][directory]]` link, and finds items by
`^\*\* <N> `. Those two shapes are a contract between the two repositories; `render_org` in
`src/info_triage/sync.py` says so, and so does `ps/info-triage--item-directory`.

## The minute after a sync

`sync.sh` prints `Synchronization complete — the inbox is ready to review now` and then keeps running
for about a minute, counting through the items. That second phase looks for each item's possible
neighbours in the plans and the notes, and writes a section only the agent cares about. **Start
reading as soon as the ready line appears.**

Working the queue while it runs is safe, including dropping items: a dropped item is reported as
`Item is no longer here, leaving it` and the run carries on. When it finishes it rewrites `triage.md`
alone, so the `triage.org` buffer never changes underneath the reader.

Where it looks, how to switch it off, and why a sync started from Emacs should take its settings
from the file and not from a shell variable are in
[`architecture/sync.md`](architecture/sync.md#the-annotation-pass).

## In VS Code

Every relative link in `triage.md` resolves from the route's directory, so preview navigation works.
One setting makes it stay in preview instead of jumping to source:

```json
"markdown.preview.openMarkdownLinks": "inPreview"
```

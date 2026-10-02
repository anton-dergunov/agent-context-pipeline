# Reviewing the inbox

`sync.sh` leaves each route's queue in `~/info-triage-inbox/<route>/`: the item directories and two
generated views of them. Their shape, shared numbering and the handling of repeat captures are
specified in [`architecture/sync.md`](architecture/sync.md). This page is about working the queue.

- `triage.md` is for the agent: every item's `index.md`, in order, one `### N — <id>` section each.
- `triage.org` is for a person: one or two lines per item, each linking to the item's index and its
  directory. It is an Org file, and it reads well enough as plain text.

Both are regenerated on every sync and must not be edited. Nothing in them carries state.

## With an agent

The intended shape is two windows side by side: the queue for you to scan, and a coding-agent
session beside it to file what is worth keeping.

1. `./sync.sh`. Start reading as soon as it prints that the inbox is ready; see
   [the minute after a sync](#the-minute-after-a-sync).
2. Skim the queue and drop what is not worth keeping by deleting its directory.
3. Tell the agent which items to deal with, by number: "file items 1, 5 and 10 from
   `~/info-triage-inbox/info/triage.md`". Each item's section tells it what was captured, what was
   retrieved and how long each body is, so it opens a body only when it needs one.
4. When an item has been filed, delete or move its directory. That is the whole acknowledgement: the
   next sync removes it from the server. If the original message is edited later, the item comes
   back.

Two things worth telling the agent once, in whatever instructions file it reads:

- Read `triage.md`, never `triage.org`. An editor integration advertises whatever file is on screen,
  and `triage.org` is on screen for the whole session while holding strictly less. It opens with a
  note saying so.
- `capture/` and every `raw/` directory inside an item are provenance for you, not input for it:
  original payloads, source HTML, PDFs and media. They are large and they are untrusted third-party
  text.

## The item number

The number in a heading is positional and regenerated on every rebuild, so after dropping items the
two views have to be renumbered together:

```bash
./sync.sh --regenerate      # no network; rewrites both views from the items on disk
```

The directory name (`2026-08-18_4`) is what does not change, which is why a filing decision is best
recorded against both.

A repeat capture of a link carries a `dup` tag and `dup of N`. File it once, then drop both
directories.

## The minute after a sync

When the neighbour pass is switched on, `sync.sh` prints
`Synchronization complete — the inbox is ready to review now` and then keeps running for a while,
counting through the items. That second phase looks for each item's possible neighbours in your
notes, and writes a section only the agent cares about. **Start reading as soon as the ready line
appears.**

Working the queue while it runs is safe, including dropping items: a dropped item is reported as
`Item is no longer here, leaving it` and the run carries on. When it finishes it rewrites `triage.md`
alone, so `triage.org` never changes underneath the reader.

Where it looks and how to switch it on are in
[`architecture/sync.md`](architecture/sync.md#the-annotation-pass).

## In VS Code

Every relative link in `triage.md` resolves from the route's directory, so preview navigation works.
One setting makes it stay in preview instead of jumping to source:

```json
"markdown.preview.openMarkdownLinks": "inPreview"
```

## In Emacs (optional)

Emacs is not required, but `triage.org` is an Org file for a reason: with a little glue the queue
becomes a read-only buffer with single-key commands. That glue is not in this repository. It lives
in [agentic-org-planner](https://github.com/anton-dergunov/agentic-org-planner), an Emacs
configuration for planning in Org, because it leans on that configuration's window management:
see its `docs/Info-triage.org` and `lisp/ps-info-triage.el`. Two settings point it here:

```elisp
(setq ps/info-triage-directory   "~/info-triage-inbox/info/"
      ps/info-triage-sync-script "~/path/to/agent-context-pipeline/sync.sh")
```

It points at **one route's** directory, not at the inbox root: each route holds its own items with
its own `triage.org` beside them. Point the setting at another route's directory to work that one.

| Key | Does |
|---|---|
| `RET` | follows the link at point: Markdown and JSON render in Emacs, images open inline, PDFs and video go to the desktop |
| `n` / `p` | next and previous item |
| `d` | drops the item: moves its directory to the Trash, then renumbers both views |
| `s` | runs `sync.sh` in the background, then refreshes the queue |
| `g` | `sync.sh --regenerate` |
| `e` | opens the item's directory in an external editor |
| `b` / `f` | back and forward through what was opened |

`d` deletes to the Trash instead of unlinking, because the decision is made at a glance and a glance
is sometimes wrong. The next sync propagates the removal to the server either way.

Two shapes in `triage.org` are a contract between the two repositories: the item's directory is read
back out of the `[[file:<id>/][directory]]` link, and items are found by `^\*\* <N> `. `render_org`
in `src/info_triage/sync.py` says so, and so does `ps/info-triage--item-directory`.

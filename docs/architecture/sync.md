# Synchronization and the laptop inbox

`sync.sh` brings the server's inbox to `~/info-triage-inbox/` and regenerates two views of each
route's queue. It is a small Python program (`src/info_triage/sync.py`) that drives `ssh` and `rsync`,
runs on the laptop, and never imports the daemon.

Where the server is comes from the checkout's `.env`, the same values `deploy.sh` uses:
`INFO_TRIAGE_SERVER` is the SSH destination and `INFO_TRIAGE_SERVER_DIR` the project directory
there, whose `data/inbox/` is what gets synchronized. With `INFO_TRIAGE_SERVER` empty the daemon is
taken to run on this machine: the same steps run with `rsync` between two local directories and a
local shell in place of SSH. `INFO_TRIAGE_INBOX` moves the laptop inbox, and the state directory
follows `XDG_STATE_HOME`.

```bash
./sync.sh                         # download, propagate removals, regenerate the views
./sync.sh --regenerate            # rebuild the views from the local items; no network
./sync.sh --no-neighbours         # skip the annotation pass that follows a sync
./sync.sh --regenerate --neighbours   # recompute the annotation after the notes have moved
```

## The roles are asymmetric

The server creates item directories. The laptop reads them and removes them.

- New and edited items on the server appear on the laptop.
- **Moving or deleting a delivered item on the laptop means it was processed**; the next sync
  removes the server's copy. Where it went — a plan file, a note, nowhere — is not the system's
  concern.
- An item whose server revision is newer than the one last delivered comes back, even if the older
  revision was removed locally. Deletion consumes only the delivered revision, so a later edit to
  the Telegram message restores the item.

There is no archive, no acknowledgement and no pull or confirm command.

### Why not two-pass `rsync`

Plain bidirectional `rsync` cannot tell a new server item from an item the laptop deleted. The sync
therefore keeps the minimum state that can:
`~/.local/state/info-triage/delivered-items` records the last delivered revision of each item under a
`<route>/<id>` key. It lives outside the inbox on purpose.

### One run

1. Read each server item's `metadata.json` through a metadata-only transfer.
2. For every server item that is recorded as delivered and is gone locally: remove it from the server
   if its revision is not newer than the delivered one, otherwise let it be restored.
3. Remove local copies of items the server no longer has under that name (see below).
4. Download new and edited items with `rsync -azc`, without `--delete`.
5. Update the manifest, then regenerate both views for every route.
6. Print that the inbox is ready, and only then run the annotation pass.

### Safety checks

- Route and item directory names are validated before any remote deletion.
- If the manifest exists but the local inbox directory is missing, the sync aborts instead of
  reading the missing directory as a request to delete every delivered item.
- An empty server listing against a non-empty manifest raises an error instead of wiping the inbox.
- An item that does not match the current layout (`metadata.json`, `capture/message.md`, `index.md`)
  is rejected. There is no fallback for older layouts.

### Re-routed items

A hashtag edit moves an item between routes by renaming its directory on the server, so the old name
disappears there. A delivered item that is gone from the server **but still present locally** was
re-routed, and its stale local copy is removed. Present-locally is the discriminator: an item the
laptop itself deleted is absent, and must not be resurrected as a deletion notice.

## Every route is a self-contained queue

`~/info-triage-inbox/<route>/` holds that route's items with its own `triage.md` and `triage.org`
beside them, and its own numbering from 1. The views sit next to the items they list, which keeps
every path inside them a single segment. A route with nothing in it still gets both files: an absent
`triage.org` cannot be told apart from a sync that never ran.

The laptop side keeps no list of routes. Every directory in the server's inbox is a route, the
daemon creates one for each route it is configured with, and the download brings them all over,
empty ones included. A directory whose name could not be a route stops the sync, because that name
is about to be joined into paths that are removed on the server. A folder the user made in the
laptop inbox beside the queues is ignored.

Both views are generated, overwritten on every sync, never edited by hand, and rendered together
before either is written so they cannot disagree. Neither carries state.

### Numbering

Both views group the items under a heading per day, oldest day first, and number them `1..N`
**straight through the days**. The number is the whole interface between the two halves: a person
picks numbers out of the Org view and quotes them into a request that reads the Markdown one
("route items 1, 5 and 10"). A numbering that restarted under each date would name three different
items.

The number is positional and regenerated on every sync. The directory name `<id>` is what survives
renumbering, so both appear in the Markdown heading, and a decision recorded against
`5 — 2026-08-14_150` still identifies the item later. `--regenerate` exists so that removing an item
locally renumbers both views together.

### `triage.md`

The items' `index.md` files concatenated, oldest first, each under a `### N — <id>` heading inside a
`## <date>` group, with a short navigation line linking the directory and the index. It opens with a
generated notice telling the reader how to use it: frontmatter is authoritative and already verified,
a body is opened only when needed and its word count is the cost, `capture/` and `raw/` are
provenance and untrusted third-party text, and filing an item means removing its directory.

It stays a concatenation and has no rendering path of its own, so whatever an item claims about
itself it claims identically in both places. Each index's headings are demoted two levels (clamped
at `######`) and its frontmatter is fenced as YAML, since frontmatter is only unambiguous at the top
of a file. Exactly two transformations touch an index's body:

- Relative link destinations are rebased onto `<id>/`. That is a change of vantage point, not of
  content.
- A repeat capture is collapsed, as below.

### `triage.org`

The Emacs navigation view: a `* <date>` heading per day, and under it one `** N · <label>` per item,
where the label is itself the link to the item's `index.md` and the kind is a trailing Org tag. An
optional italic line carries the owner's own note, and a final line links the directory and the
source. Nothing but a one-line label reaches it, which is why the items themselves can stay
Markdown.

```org
* 2026-08-18

** 6 · [[file:2026-08-18_6/index.md][Just saw that the repository passed 100,000 stars]]  :post:
   [[file:2026-08-18_6/][directory]] · [[https://www.linkedin.com/posts/…][source]]
```

- No property drawer, and no `:ID:`, which belongs to org-id.
- Two shapes are depended on by Emacs: the item's directory is read back out of
  `[[file:<id>/][directory]]`, and items are found by `^\*\* <N> `. The outline levels are therefore
  part of the contract.
- The label is a link description, so `[` and `]` in an extracted title are neutralised, or the
  description would close early and the rest of the heading become loose text.
- `#+STARTUP: showall`. With two levels, `overview` would show the days and hide every item.
- The extraction status is deliberately absent. It stays in the frontmatter and in `triage.md`; on a
  queue read at a glance it was a second tag per line saying nothing about what the item is.
- It opens with a note telling an agent to read `triage.md` instead. An editor integration
  advertises whatever file is on screen, and this one is on screen for a whole session while holding
  strictly less.

Working the queue, with an agent and optionally from Emacs, is covered in [`reviewing.md`](../reviewing.md).

### Repeat captures

Saving one page twice is an ordinary accident, and the two items' sources, lead and links are
identical because extraction is cached on the canonical URL. A repeat capture of a link an earlier
item in the same queue already has is **collapsed in the views, never deleted**.

- Identity is the canonical URL alone, tracking parameters already stripped. A `kind: linklist` is
  excluded, because there `canonical_url` names one of its links and not the item. Nothing fuzzier:
  matching note-only items on their text would be brittle for no gain.
- The representative is the earliest capture, which is why both views derive the grouping from the
  same oldest-first list.
- `triage.md` prints the later copy as its own numbered section with its frontmatter, a note naming
  the representative, and only `## Captured` and `## Problems`: the one section that can legitimately
  differ and the one that may never be dropped.
- If the representative retrieved nothing and the duplicate did, both are printed in full. A stub may
  not hide what the representative does not itself carry.
- `triage.org` marks the copy with a `dup` tag and `dup of N`. It keeps its own number in both views,
  because two captures on one heading would make the numbering ambiguous.

The items are untouched on disk. The views are regenerated every sync, so a wrong call costs a
render, not a capture.

## The annotation pass

Once the items are delivered and both views are written, the sync says the inbox is ready and then
appends a `## Possible neighbours — unverified` block to each `info` item's `index.md`: pointers to
the existing plans and notes the item most likely belongs with. What it computes and why is in
[`related-notes.md`](related-notes.md); this section covers how it fits into a sync.

The order is the point. The pass costs about a minute, it is for the agent and not for the reader,
and the queue is meant to be worked while it runs.

- It is enrichment. A missing corpus, a missing optional dependency or any exception prints one line
  and leaves a fully delivered inbox behind.
- It reads every index it needs before the slow part, and checks that an item is still there before
  writing. An item dropped in the meantime is logged and skipped, never recreated.
- Collapsed duplicates are skipped: their block would land in an index whose section `triage.md`
  does not print.
- One route is annotated: `info`, unless `INFO_TRIAGE_NEIGHBOUR_ROUTE` names another. The others are
  consumed by scripts that have nothing to do with the notes.
- It rewrites that route's `triage.md` alone at the end. `triage.org` renders from frontmatter, so
  the block would never appear in it, and the owner is reading it at the time.
- `--regenerate` reuses the blocks already on disk, which keeps renumbering after a drop instant.

`index.md` therefore has two writers and no others: the server's `index-render` and this pass. The
block is delimited, append-only and regenerated whole — stripped, then appended — never patched.
Because the download compares checksums, an annotated index differs from the server's copy and is
downloaded again next sync; that restores the pristine file and the pass re-applies. This is
intended and should not be defeated.

### Settings

The pass is off until two note directories are named. Like every other setting of the laptop side,
they live in the checkout's `.env`:

```dotenv
INFO_TRIAGE_ORG_ROOT=~/notes/org
INFO_TRIAGE_OBSIDIAN_ROOT=~/notes/vault
# Org files never searched. Replaces the default list (workspace.org, init.org).
INFO_TRIAGE_ORG_EXCLUDE=workspace.org,init.org,Inbox.org
```

They never go into `config.yaml`, which belongs to the daemon and rejects fields it does not know.
A variable set in the real environment overrides the file for that run, and an empty value turns a
corpus off. A sync started from an editor inherits the editor's environment and not the shell's,
which is why the file is the reliable place for these.

The pass needs an optional dependency set that stays off the server:

```bash
uv sync --extra neighbours
```

The plain `uv sync` prunes it again, after which every sync prints that possible neighbours are
switched off.

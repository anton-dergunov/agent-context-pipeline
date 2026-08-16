# Reviewing the inbox from Emacs

`sync.sh` writes two views of the same items, both generated and both
overwritten on every sync:

- **`triage.md`** — the whole contract, one `## N — <id>` section per item. This
  is what `/route` reads.
- **`triage.org`** — navigation only: one foldable heading per item, a
  `:PROPERTIES:` drawer, and two links. No extracted text reaches it, which is
  the whole reason the items themselves stay Markdown (`Message-contract-design.md`
  §4.4): Org gives `*`, `_` and `[[` structural meaning, and an escaper bug
  would corrupt items rather than merely look wrong.

Neither file carries state. **Deleting an item's directory is the signal that it
was processed**; a mark written into a generated file would be lost on the next
sync.

## The split

Emacs owns the queue. VS Code owns the artifacts — it already renders PDF,
video, images and Markdown preview with no configuration, and an item directory
is exactly the kind of mixed-media tree it is good at. Making Emacs a file
browser for `extracted/*/raw/` would be a lot of work to arrive somewhere worse.

The intended shape is two windows side by side: `triage.org` in Emacs to choose
items and drive `/route`, the inbox in VS Code to look at what an item actually
contains.

## Setup

```elisp
(load "~/projects/tools/info-triage/emacs/info-triage.el")
(global-set-key (kbd "C-c i") #'info-triage-inbox)
```

| Command | Does |
|---|---|
| `info-triage-inbox` | opens `~/info-triage-inbox/triage.org` |
| `info-triage-sync` | runs `sync.sh` in a compilation buffer, then reverts the overview |
| `info-triage-open-externally` | opens **the item at point** in VS Code; with `C-u`, the whole inbox |
| `info-triage-back` | returns from a link you followed (`org-mark-ring-goto`) |

Three `defcustom`s cover the paths: `info-triage-inbox-directory`,
`info-triage-sync-script`, `info-triage-editor-command`.

## Moving around

From a heading in `triage.org`:

- `C-c C-o` on `[[file:2026-08-14_150/index.md][index]]` opens the item's index
- `C-c C-o` on `[[file:2026-08-14_150/][directory]]` opens it in Dired
- `info-triage-open-externally` opens that same directory in VS Code
- `info-triage-back` (`C-c &`) returns to where you were
- `C-c C-n` / `C-c C-p` move between items; the file opens folded

`:ITEM:` is the number you pass to `/route` ("route items 1, 5 and 10"). It is
positional and regenerated on every sync. `:DIR:` is the directory name, which
does not change — so a routing decision recorded against `5 — 2026-08-14_150`
still identifies the right item after the next sync renumbers it.

## In VS Code

Every relative link in `triage.md` resolves from the inbox root, so preview
navigation works. One setting makes it stay in preview rather than jumping to
source:

```json
"markdown.preview.openMarkdownLinks": "inPreview"
```

# related-notes prototype

Measurement code for `docs/Related-notes-design.md`. Not wired into the daemon and
not covered by the test suite — it exists so the numbers in that document can be
re-derived and the thresholds re-fitted when the inbox stops being ML-only.

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install sentence-transformers torch
./.venv/bin/python corpus.py     # notes/org + notes/obsidian -> units.jsonl
./.venv/bin/python embed.py mps  # -> vecs.npy  (use `cpu` off Apple silicon)
./.venv/bin/python eval4.py      # 22 queries -> results4.json
```

`search.py:Index.related()` is the function a `related-notes` pipeline step would
call. `corpus.py` holds the Org and Markdown unit parsing, which is the part most
likely to need adjusting as the notes change shape.

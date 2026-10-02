"""Short rerank query + a sweep of the abstain threshold."""

import json
import re
import time
from pathlib import Path

from items import all_items
from search import Index


def short(q: str, words: int = 28) -> str:
    """Title and the first sentence of the lead — the item's actual subject."""
    head = re.split(r"(?<=[.!?])\s", q)
    out, n = [], 0
    for s in head:
        out.append(s)
        n += len(s.split())
        if n >= words:
            break
    return " ".join(out)


if __name__ == "__main__":
    idx = Index()
    items = all_items()
    rows = []
    t0 = time.time()
    for it in items:
        rq = short(it["query"])
        r = idx.related(it["query"], k=3, pool=40, min_score=-99, rerank_query=rq)
        rows.append({**it, **r, "rerank_query": rq})
        print(f"\n=== {it['id']}  [{it['kind']}]  top_ce={r['top_ce']}\n    RQ: {rq[:100]}")
        for lab in ("plans", "vault"):
            for h in r[lab]:
                print(f"  {lab:5s} ce={h['ce']:6.3f} {h['file']}:{h['line']} | {h['title'][:64]}")
    Path("results4.json").write_text(json.dumps(rows, indent=1))
    print(f"\n{len(items)} queries, {time.time() - t0:.0f}s")

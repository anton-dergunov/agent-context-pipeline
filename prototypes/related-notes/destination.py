"""Destination hint: which plan file does this item belong to?

Reranks the item against the 32 file charters — the prose that states what each file
is for. That is exactly the text `/route`'s destination step reads, so this asks the
same question against the same evidence, just without an agent turn.
"""
import json
from pathlib import Path

import rerank
from eval3 import short
from items import all_items

HERE = Path(__file__).parent
units = [json.loads(l) for l in (HERE / "units.jsonl").open()]
charters = [u for u in units if u["kind"] == "org-charter"]


def destinations(query: str, k: int = 2) -> list[dict]:
    rq = short(query)
    scores = rerank.score(rq, [c["text"][:1500] for c in charters])
    rows = sorted(zip(charters, scores), key=lambda z: -z[1])[:k]
    return [{"file": c["file"], "ce": round(float(s), 2)} for c, s in rows]


if __name__ == "__main__":
    out = {}
    for it in all_items():
        d = destinations(it["query"])
        out[it["id"]] = d
        print(f"{it['id']:26s} " + "  ".join(f"{r['file']}({r['ce']:+.1f})" for r in d))
    (HERE / "destinations.json").write_text(json.dumps(out, indent=1))

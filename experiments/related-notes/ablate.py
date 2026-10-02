"""Does the embedding add anything over BM25 alone? Rank of the hand-judged
correct unit under each retrieval mode."""
import json
import numpy as np
from search import Index, tokenize
from items import all_items
from eval3 import short

# hand-judged correct unit per query, as file:line (from the eval in the design doc)
GOLD = {
 "2026-08-18_1":  "ML/Generative_AI.org:1068",
 "2026-08-18_5":  "ML/Generative_AI.org:550",
 "2026-08-18_7":  "ML/Generative_AI.org:304",
 "2026-08-18_8":  "ML/Ranking.org:758",
 "2026-08-18_9":  "ML/Generative_AI.org:56",
 "2026-08-18_10": "ML/Systems.org:324",
 "2026-08-18_12": "ML/Ranking.org:375",
 "2026-08-18_13": "ML/Generative_AI.org:735",
 "2026-08-18_15": "ML/Generative_AI.org:289",
 "s2-chopin-fingering": "Play/Piano.org:22",
 "s3-hsk-anki":   "Play/Languages.org:15",
 "s5-darienzo":   "Play/Tango.org:65",
 "s6-feed-ranking-interview": "ML/Ranking.org:86",
 "s7-ab-peeking": "ML/Statistics.org:46",
 "s8-rust-ownership": "Work/Programming.org:352",
}

idx = Index()
key = [f"{u['file']}:{u['line']}" for u in idx.units]


def rank_of(scores, gold):
    order = np.argsort(-scores)
    for r, i in enumerate(order):
        if key[i] == gold:
            return r + 1
    return None


rows = []
for it in all_items():
    gold = GOLD.get(it["id"])
    if not gold:
        continue
    dense = idx.vecs @ idx.encode_query(it["query"])
    lex = idx.bm25.score(tokenize(it["query"]))
    lex_short = idx.bm25.score(tokenize(short(it["query"])))
    rows.append((it["id"], rank_of(lex, gold), rank_of(lex_short, gold), rank_of(dense, gold)))

print(f"{'item':28s} {'BM25(full)':>11s} {'BM25(title)':>12s} {'dense':>7s}")
for r in rows:
    f = lambda v: str(v) if v else "miss"
    print(f"{r[0]:28s} {f(r[1]):>11s} {f(r[2]):>12s} {f(r[3]):>7s}")


def at(rs, k):
    return sum(1 for v in rs if v and v <= k)


for name, col in (("BM25(full)", 1), ("BM25(title)", 2), ("dense", 3)):
    rs = [r[col] for r in rows]
    print(f"{name:12s} hit@1={at(rs,1)}/{len(rs)}  hit@5={at(rs,5)}/{len(rs)}  "
          f"hit@40={at(rs,40)}/{len(rs)}  median={sorted(v for v in rs if v)[len(([v for v in rs if v]))//2]}")

"""Hybrid BM25 + dense retrieval over the plan/vault units, fused with RRF."""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
STOP = set("""a an the and or but if then than that this these those of in on at to for from by
with without as is are was were be been being it its it's he she they them their there here
what which who whom how why when where all any both each few more most other some such no nor
not only own same so too very can will just don should now about into over under again further
i you your my we our us me him her his do does did doing have has had having would could may
might must shall me one two also new use used using make makes made get gets got like via per
you're we're isn't doesn't""".split())
TOKEN = re.compile(r"[a-z0-9][a-z0-9+.#_-]*")


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP and len(t) > 1]


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.6):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.len = np.array([len(d) for d in docs], dtype=np.float32)
        self.avg = float(self.len.mean())
        self.post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, d in enumerate(docs):
            for term, tf in Counter(d).items():
                self.post[term].append((i, tf))
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5))
                    for t, p in self.post.items()}

    def score(self, query: list[str]) -> np.ndarray:
        s = np.zeros(self.N, dtype=np.float32)
        for term in set(query):
            post = self.post.get(term)
            if not post:
                continue
            idf = self.idf[term]
            idx = np.fromiter((i for i, _ in post), dtype=np.int64, count=len(post))
            tf = np.fromiter((f for _, f in post), dtype=np.float32, count=len(post))
            denom = tf + self.k1 * (1 - self.b + self.b * self.len[idx] / self.avg)
            s[idx] += idf * (tf * (self.k1 + 1)) / denom
        return s


class Index:
    def __init__(self):
        self.units = [json.loads(l) for l in (HERE / "units.jsonl").open()]
        self.vecs = np.load(HERE / "vecs.npy")
        self.bm25 = BM25([tokenize(u["text"]) for u in self.units])
        self._embedder = None

    def embedder(self):
        if self._embedder is None:
            from embed import load
            self._embedder = load()
        return self._embedder

    def encode_query(self, query: str):
        import embed
        return embed.encode_queries(self.embedder(), [query])[0]

    def candidates(self, query: str, pool: int = 60, rrf_k: int = 60) -> list[dict]:
        """Stage 1: cheap recall. BM25 and dense, fused with RRF, no gating."""
        qvec = self.encode_query(query)
        dense = self.vecs @ qvec
        lex = self.bm25.score(tokenize(query))
        rr: dict[int, float] = defaultdict(float)
        for r, i in enumerate(np.argsort(-dense)[:pool]):
            rr[int(i)] += 1.0 / (rrf_k + r + 1)
        for r, i in enumerate(np.argsort(-lex)[:pool]):
            if lex[i] > 0:
                rr[int(i)] += 1.0 / (rrf_k + r + 1)
        return [{**self.units[i], "dense": round(float(dense[i]), 3),
                 "bm25": round(float(lex[i]), 2), "fused": round(f, 5)}
                for i, f in sorted(rr.items(), key=lambda kv: -kv[1])]

    def related(self, query: str, k: int = 3, pool: int = 50, min_score: float = 0.0,
                per_file: int = 1, device: str = "cpu", rerank_query: str | None = None) -> dict:
        """Stage 2: cross-encoder rerank, then an absolute-score abstain gate.

        Recall uses the long query — title, intent and lead together. Reranking uses
        a short one: a multi-topic query saturates the cross-encoder at 1.0 for every
        candidate, which destroys exactly the ordering the rerank exists to produce.
        """
        import rerank
        cands = self.candidates(query, pool=pool)[:pool]
        if not cands:
            return {"plans": [], "vault": [], "destination": None}
        scores = rerank.score(rerank_query or query,
                              [c["text"][:1800] for c in cands], device=device)
        for c, s in zip(cands, scores):
            c["ce"] = round(float(s), 2)
        cands.sort(key=lambda c: -c["ce"])

        out: dict[str, list[dict]] = {"plans": [], "vault": []}
        seen: Counter = Counter()
        for c in cands:
            if c["ce"] < min_score:
                continue
            bucket = "plans" if c["source"] == "org" else "vault"
            if c["kind"] == "org-charter":
                continue           # a charter is a destination signal, not a duplicate
            if seen[c["file"]] >= per_file or len(out[bucket]) >= k:
                continue
            seen[c["file"]] += 1
            out[bucket].append(c)
        # destination: the plan file whose charter or best task scored highest
        best = max((c for c in cands if c["source"] == "org"),
                   key=lambda c: c["ce"], default=None)
        out["destination"] = ({"file": best["file"], "ce": best["ce"]}
                              if best is not None and best["ce"] >= min_score else None)
        out["top_ce"] = round(float(max(c["ce"] for c in cands)), 2)
        return out

    def search(self, query: str, k: int = 5, pool: int = 60, rrf_k: int = 60,
               min_dense: float = 0.62, min_bm25_ratio: float = 0.25,
               per_file: int = 2, per_source: int | None = None) -> list[dict]:
        qvec = self.encode_query(query)
        dense = self.vecs @ qvec
        lex = self.bm25.score(tokenize(query))

        d_rank = np.argsort(-dense)[:pool]
        l_rank = np.argsort(-lex)[:pool]
        rr: dict[int, float] = defaultdict(float)
        for r, i in enumerate(d_rank):
            rr[int(i)] += 1.0 / (rrf_k + r + 1)
        for r, i in enumerate(l_rank):
            if lex[i] > 0:
                rr[int(i)] += 1.0 / (rrf_k + r + 1)

        lex_max = float(lex.max()) if lex.size else 0.0
        out, seen_file = [], Counter()
        for i, fused in sorted(rr.items(), key=lambda kv: -kv[1]):
            u = self.units[i]
            # abstain gate: a candidate must be strong on at least one of the two axes
            strong_dense = float(dense[i]) >= min_dense
            strong_lex = lex_max > 0 and float(lex[i]) >= min_bm25_ratio * lex_max
            if not (strong_dense or strong_lex):
                continue
            if seen_file[u["file"]] >= per_file:
                continue
            seen_file[u["file"]] += 1
            out.append({**u, "dense": round(float(dense[i]), 3),
                        "bm25": round(float(lex[i]), 2), "fused": round(fused, 5)})
            if len(out) == k:
                break
        return out

"""How does retrieval hold up as the vault grows? Subsample the vault to several
sizes, keep the Org plans fixed, and watch the gold answer's rank."""

import json
import random

import numpy as np
from ablate import GOLD
from items import all_items
from search import BM25, tokenize

VAULT_GOLD = {
    "s7-ab-peeking": "Statistics/Peeking and Sequential Testing.md",
    "s4-soy-protein": "Nutrition/Macronutrients.md",
    "2026-08-18_13": "ML & AI/Concepts/Detecting machine-generated text.md",
    "2026-08-18_12": "ML & AI/Concepts/Recommendation systems.md",
    "s2-chopin-fingering": "Piano/Piano technique.md",
    "s5-darienzo": "Tango/My favourite tango music organized in tandas.md",
}

units = [json.loads(ln) for ln in open("units.jsonl")]
org = [u for u in units if u["source"] == "org"]
vault = [u for u in units if u["source"] == "vault"]
queries = {it["id"]: it["query"] for it in all_items()}
print(f"org={len(org)} vault={len(vault)}")


def run(frac, seed=0):
    rnd = random.Random(seed)
    keep = vault if frac >= 1 else rnd.sample(vault, int(len(vault) * frac))
    # keep every gold note in the sample, so we measure interference not absence
    have = {id(u) for u in keep}
    keep = keep + [u for u in vault if u["file"] in VAULT_GOLD.values() and id(u) not in have]
    corpus = org + keep
    bm = BM25([tokenize(u["text"]) for u in corpus])
    okey = [f"{u['file']}:{u['line']}" for u in corpus]
    fkey = [u["file"] for u in corpus]
    src = [u["source"] for u in corpus]

    def ranks(gold_map, by_file):
        out = []
        for qid, gold in gold_map.items():
            if qid not in queries:
                continue
            s = bm.score(tokenize(queries[qid]))
            order = np.argsort(-s)
            # bucket-separated: rank within its own source, as the design scores them
            want = "vault" if by_file else "org"
            r = 0
            for i in order:
                if src[i] != want:
                    continue
                r += 1
                if (fkey[i] == gold) if by_file else (okey[i] == gold):
                    out.append(r)
                    break
            else:
                out.append(None)
        return out

    return len(corpus), ranks(GOLD, False), ranks(VAULT_GOLD, True)


def summarise(rs):
    ok = [r for r in rs if r]
    return f"hit@5={sum(1 for r in ok if r <= 5)}/{len(rs)} hit@40={sum(1 for r in ok if r <= 40)}/{len(rs)} median={sorted(ok)[len(ok) // 2] if ok else '-'}"


print(f"\n{'vault frac':>10} {'corpus':>8}  {'ORG-side gold':<38} {'VAULT-side gold'}")
for frac in (0.1, 0.25, 0.5, 1.0):
    n, o, v = run(frac)
    print(f"{frac:>10.0%} {n:>8}  {summarise(o):<38} {summarise(v)}")


def run_split(frac, seed=0):
    """One BM25 index per corpus, so vault growth cannot move Org-side IDF."""
    bo = BM25([tokenize(u["text"]) for u in org])
    okey = [f"{u['file']}:{u['line']}" for u in org]
    out = []
    for qid, gold in GOLD.items():
        if qid not in queries:
            continue
        order = np.argsort(-bo.score(tokenize(queries[qid])))
        out.append(next((r + 1 for r, i in enumerate(order) if okey[i] == gold), None))
    return out


print("\nSeparate BM25 index per corpus — Org-side gold, at every vault size:")
for frac in (0.1, 0.25, 0.5, 1.0):
    print(f"{frac:>10.0%}  {summarise(run_split(frac))}")

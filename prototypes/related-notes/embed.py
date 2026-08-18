"""Corpus embeddings. Prototype path uses sentence-transformers; the NAS path is ONNX."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

HERE = Path(__file__).parent
MODEL = "intfloat/multilingual-e5-small"


def load(device: str = "mps") -> SentenceTransformer:
    return SentenceTransformer(MODEL, device=device)


def encode_docs(m, texts: list[str], batch: int = 64) -> np.ndarray:
    return m.encode(["passage: " + t for t in texts], batch_size=batch,
                    normalize_embeddings=True, show_progress_bar=True)


def encode_queries(m, texts: list[str], batch: int = 32) -> np.ndarray:
    return m.encode(["query: " + t for t in texts], batch_size=batch,
                    normalize_embeddings=True)


if __name__ == "__main__":
    units = [json.loads(l) for l in (HERE / "units.jsonl").open()]
    m = load(sys.argv[1] if len(sys.argv) > 1 else "mps")
    t0 = time.time()
    vecs = encode_docs(m, [u["text"] for u in units])
    dt = time.time() - t0
    np.save(HERE / "vecs.npy", vecs)
    print(f"encoded {len(units)} units in {dt:.1f}s ({len(units)/dt:.0f}/s) -> {vecs.shape}")

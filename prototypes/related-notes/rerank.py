"""Cross-encoder rerank stage. This is what makes the abstain decision possible:
a bi-encoder's cosine sits in a narrow band, a cross-encoder's logit does not."""
from __future__ import annotations

import numpy as np
from sentence_transformers import CrossEncoder

MODEL = "BAAI/bge-reranker-base"   # 278M XLM-R base: multilingual, NAS-sized
_model = None


def load(device: str = "cpu") -> CrossEncoder:
    global _model
    if _model is None:
        import torch
        torch.set_num_threads(6)
        _model = CrossEncoder(MODEL, device=device, max_length=384)
    return _model


def score(query: str, docs: list[str], device: str = "cpu", batch: int = 16) -> np.ndarray:
    m = load(device)
    import torch
    # Raw logits, not the sigmoid: the probability saturates at both ends, and the
    # abstain gate needs a score that still moves when the model is merely unsure.
    raw = m.predict([(query, d) for d in docs], batch_size=batch,
                    show_progress_bar=False, activation_fn=torch.nn.Identity())
    return np.asarray(raw, dtype=np.float32).reshape(-1)

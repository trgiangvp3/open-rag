"""Cross-encoder reranker using BAAI/bge-reranker-v2-m3.

Two-stage retrieval: semantic top-N → cross-encoder re-score → top-K.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from config import EMBEDDING_DEVICE, RERANKER_MODEL

logger = logging.getLogger(__name__)


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class Reranker:
    def __init__(self, model_name: str = RERANKER_MODEL, device: str = EMBEDDING_DEVICE):
        resolved = _resolve_device(device)
        logger.info("Loading reranker '%s' on %s ...", model_name, resolved)
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name, device=resolved)
        logger.info("Reranker ready.")

    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        """Re-score (query, passage) pairs; return top_k sorted by rerank_score desc."""
        if not chunks:
            return []

        pairs = [(query, c["text"] or "") for c in chunks]
        scores = self.model.predict(pairs)

        ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        return [
            {**chunk, "rerank_score": round(float(score), 4)}
            for chunk, score in ranked[:top_k]
        ]


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker()

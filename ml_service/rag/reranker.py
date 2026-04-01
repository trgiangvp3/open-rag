"""Cross-encoder reranker using BAAI/bge-reranker-v2-m3.

Two-stage retrieval: semantic top-N → cross-encoder re-score → top-K.

Lazy-loaded: the model is only loaded on first rerank request to save ~2 GB RAM
when reranking is not used.
"""

from __future__ import annotations

import gc
import logging
import threading

import torch

from config import EMBEDDING_DEVICE, RERANKER_MODEL

logger = logging.getLogger(__name__)

_reranker: Reranker | None = None
_reranker_lock = threading.Lock()


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    return "cuda" if torch.cuda.is_available() else "cpu"


class Reranker:
    def __init__(self, model_name: str = RERANKER_MODEL, device: str = EMBEDDING_DEVICE):
        resolved = _resolve_device(device)
        logger.info("Loading reranker '%s' on %s ...", model_name, resolved)
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name, device=resolved)
        self.model.model.half()  # fp16 — halves memory usage
        logger.info("Reranker ready (fp16).")

    @torch.inference_mode()
    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        """Re-score (query, passage) pairs; return top_k sorted by rerank_score desc."""
        if not chunks:
            return []

        pairs = [(query, c["text"] or "") for c in chunks]
        scores = self.model.predict(pairs)

        ranked = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        result = [
            {**chunk, "rerank_score": round(float(score), 4)}
            for chunk, score in ranked[:top_k]
        ]
        del scores, pairs
        if len(chunks) > 50:
            gc.collect()
        return result


def get_reranker() -> Reranker:
    """Lazy singleton — model loaded only on first call."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                _reranker = Reranker()
    return _reranker

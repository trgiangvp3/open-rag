"""Cross-encoder reranker using BAAI/bge-reranker-v2-m3.

Two-stage retrieval: semantic top-N → cross-encoder re-score → top-K.

Lazy-loaded on first rerank request. Auto-unloads after MODEL_IDLE_TTL seconds
of inactivity to free ~2 GB RAM.
"""

from __future__ import annotations

import gc
import logging
import threading
import time as _time

import torch

from config import EMBEDDING_DEVICE, MODEL_IDLE_TTL, RERANKER_MODEL

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_reranker: Reranker | None = None
_last_used: float = 0.0
_timer: threading.Timer | None = None


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


def _schedule_unload() -> None:
    """Schedule an idle check after MODEL_IDLE_TTL seconds."""
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(MODEL_IDLE_TTL, _try_unload)
    _timer.daemon = True
    _timer.start()


def _try_unload() -> None:
    """Unload reranker if it hasn't been used since the timer was set."""
    global _reranker, _timer
    with _lock:
        if _reranker is None:
            return
        elapsed = _time.time() - _last_used
        if elapsed >= MODEL_IDLE_TTL:
            logger.info("Reranker idle for %ds — unloading to free RAM", int(elapsed))
            del _reranker
            _reranker = None
            _timer = None
            gc.collect()
            torch.cuda.empty_cache() if torch.cuda.is_available() else None
        else:
            _schedule_unload()


def get_reranker() -> Reranker:
    """Lazy singleton with idle unload. Model loaded on first call."""
    global _reranker, _last_used
    with _lock:
        if _reranker is None:
            _reranker = Reranker()
        _last_used = _time.time()
        _schedule_unload()
        return _reranker

"""Embedding module using sentence-transformers (bge-m3).

Auto-unloads after MODEL_IDLE_TTL seconds of inactivity to free RAM.
"""

from __future__ import annotations

import gc
import logging
import threading
import time as _time

import torch
from sentence_transformers import SentenceTransformer

from config import EMBEDDING_DEVICE, EMBEDDING_MODEL, MODEL_IDLE_TTL

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_embedder: Embedder | None = None
_last_used: float = 0.0
_timer: threading.Timer | None = None


def _resolve_device(device: str) -> str:
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


class Embedder:
    """Wrapper around sentence-transformers for text embedding."""

    def __init__(self, model_name: str = EMBEDDING_MODEL, device: str = EMBEDDING_DEVICE):
        self.device = _resolve_device(device)
        logger.info(f"Loading embedding model '{model_name}' on {self.device}...")
        self.model = SentenceTransformer(model_name, device=self.device)
        self.model.half()  # fp16 — halves memory usage
        self._dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Model loaded (fp16). Dimension: {self._dim}")

    @property
    def dimension(self) -> int:
        return self._dim

    @torch.inference_mode()
    def embed_texts(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        logger.info(f"Embedding {len(texts)} texts on {self.device}...")
        start = _time.time()
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        result = embeddings.tolist()
        del embeddings
        elapsed = _time.time() - start
        logger.info(f"Embedded {len(texts)} texts in {elapsed:.1f}s ({elapsed/len(texts):.2f}s/text)")
        if len(texts) > 50:
            gc.collect()
        return result

    @torch.inference_mode()
    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


def _schedule_unload_locked() -> None:
    """Schedule an idle check. Must be called with _lock held."""
    global _timer
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(MODEL_IDLE_TTL, _try_unload)
    _timer.daemon = True
    _timer.start()


def _try_unload() -> None:
    """Unload embedder if it hasn't been used since the timer was set."""
    global _embedder, _timer
    did_unload = False
    with _lock:
        if _embedder is None:
            return
        elapsed = _time.time() - _last_used
        if elapsed >= MODEL_IDLE_TTL:
            logger.info("Embedder idle for %ds — unloading to free RAM", int(elapsed))
            _embedder = None
            _timer = None
            did_unload = True
        else:
            # Used again in the meantime — reschedule
            _schedule_unload_locked()
    if did_unload:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def get_embedder() -> Embedder:
    """Get or create embedder. Resets idle timer on each call."""
    global _embedder, _last_used
    with _lock:
        if _embedder is None:
            _embedder = Embedder()
        _last_used = _time.time()
        _schedule_unload_locked()
        return _embedder

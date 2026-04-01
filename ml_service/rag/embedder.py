"""Embedding module using sentence-transformers (bge-m3)."""

import gc
import logging
from functools import lru_cache

import torch
from sentence_transformers import SentenceTransformer

from config import EMBEDDING_DEVICE, EMBEDDING_MODEL

logger = logging.getLogger(__name__)


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
        import time
        logger.info(f"Embedding {len(texts)} texts on {self.device}...")
        start = time.time()
        embeddings = self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        result = embeddings.tolist()
        del embeddings
        elapsed = time.time() - start
        logger.info(f"Embedded {len(texts)} texts in {elapsed:.1f}s ({elapsed/len(texts):.2f}s/text)")
        if len(texts) > 50:
            gc.collect()
        return result

    @torch.inference_mode()
    def embed_query(self, query: str) -> list[float]:
        return self.embed_texts([query])[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Singleton embedder instance."""
    return Embedder()

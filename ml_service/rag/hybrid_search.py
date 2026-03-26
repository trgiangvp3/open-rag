"""Hybrid search: BM25 + semantic via Reciprocal Rank Fusion.

BM25 indexes are built lazily per collection from ChromaDB and invalidated
whenever new chunks are indexed or deleted.
"""

from __future__ import annotations

import logging
import threading

from rank_bm25 import BM25Okapi

from rag.store import VectorStore

logger = logging.getLogger(__name__)


class HybridSearcher:
    """Manages per-collection BM25 indexes with lazy loading from ChromaDB."""

    def __init__(self, store: VectorStore):
        self._store = store
        # collection_name → (BM25Okapi, list[{text, metadata}])
        self._indexes: dict[str, tuple[BM25Okapi, list[dict]]] = {}
        self._lock = threading.Lock()

    # ── Index management ──────────────────────────────────────────────────────

    def _build_index(self, collection_name: str) -> tuple[BM25Okapi, list[dict]]:
        """Fetch all chunks from ChromaDB and build a BM25 index."""
        collection = self._store.get_or_create_collection(collection_name)
        result = collection.get(include=["documents", "metadatas"])
        texts: list[str] = result.get("documents") or []
        metadatas: list[dict] = result.get("metadatas") or []

        tokenized = [text.lower().split() for text in texts]
        bm25 = BM25Okapi(tokenized) if tokenized else BM25Okapi([[]])
        chunks = [{"text": t, "metadata": m} for t, m in zip(texts, metadatas)]
        logger.info("Built BM25 index for '%s': %d chunks", collection_name, len(chunks))
        return bm25, chunks

    def _get_index(self, collection_name: str) -> tuple[BM25Okapi, list[dict]]:
        with self._lock:
            if collection_name not in self._indexes:
                self._indexes[collection_name] = self._build_index(collection_name)
            return self._indexes[collection_name]

    def invalidate(self, collection_name: str) -> None:
        """Drop cached index so it will be rebuilt on next query."""
        with self._lock:
            self._indexes.pop(collection_name, None)

    # ── Search ────────────────────────────────────────────────────────────────

    def bm25_search(self, collection_name: str, query: str, top_k: int) -> list[dict]:
        """Return top-k chunks ranked by BM25 score."""
        bm25, chunks = self._get_index(collection_name)
        if not chunks:
            return []

        tokenized_query = query.lower().split()
        scores = bm25.get_scores(tokenized_query)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            {**chunks[i], "score": round(float(scores[i]), 4)}
            for i in top_indices
        ]

    def hybrid_search(
        self,
        collection_name: str,
        query: str,
        semantic_results: list[dict],
        top_k: int,
        k: int = 60,
    ) -> list[dict]:
        """Fuse semantic and BM25 results with Reciprocal Rank Fusion.

        RRF formula: score(d) = Σ 1 / (k + rank_i(d))
        """
        bm25_results = self.bm25_search(collection_name, query, top_k * 10)

        rrf_scores: dict[str, float] = {}
        text_to_chunk: dict[str, dict] = {}

        for rank, chunk in enumerate(semantic_results):
            key = chunk["text"]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            text_to_chunk[key] = chunk

        for rank, chunk in enumerate(bm25_results):
            key = chunk["text"]
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in text_to_chunk:
                text_to_chunk[key] = chunk

        sorted_keys = sorted(rrf_scores, key=lambda kk: rrf_scores[kk], reverse=True)
        return [
            {**text_to_chunk[key], "score": round(rrf_scores[key], 6)}
            for key in sorted_keys[:top_k]
        ]

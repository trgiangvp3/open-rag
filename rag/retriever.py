"""Retrieval pipeline: query → embed → search → return results."""

import logging

from rag.embedder import get_embedder
from rag.store import VectorStore

logger = logging.getLogger(__name__)


class Retriever:
    """Search documents using semantic similarity."""

    def __init__(self, store: VectorStore):
        self.store = store

    def search(
        self,
        query: str,
        collection: str = "documents",
        top_k: int = 5,
    ) -> list[dict]:
        embedder = get_embedder()
        query_embedding = embedder.embed_query(query)

        results = self.store.search(
            collection_name=collection,
            query_embedding=query_embedding,
            top_k=top_k,
        )

        logger.info(f"Search '{query[:50]}...' → {len(results)} results")
        return results

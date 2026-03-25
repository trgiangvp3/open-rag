"""ChromaDB vector store wrapper."""

import logging
from datetime import datetime, timezone

import chromadb
from chromadb.config import Settings

from config import CHROMA_COLLECTION, CHROMA_DIR

logger = logging.getLogger(__name__)


class VectorStore:
    """ChromaDB-based vector store for document chunks."""

    def __init__(self, persist_dir: str = str(CHROMA_DIR)):
        self.client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        logger.info(f"ChromaDB initialized at {persist_dir}")

    def get_or_create_collection(self, name: str = CHROMA_COLLECTION) -> chromadb.Collection:
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        collection_name: str,
        document_id: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        collection = self.get_or_create_collection(collection_name)
        ids = [f"{document_id}_{i}" for i in range(len(texts))]
        indexed_at = datetime.now(timezone.utc).isoformat()
        for meta in metadatas:
            meta["document_id"] = document_id
            meta["indexed_at"] = indexed_at

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info(f"Added {len(texts)} chunks to '{collection_name}' (doc: {document_id})")

    def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> list[dict]:
        collection = self.get_or_create_collection(collection_name)
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        items = []
        for i in range(len(results["ids"][0])):
            distance = results["distances"][0][i]
            score = 1.0 - distance
            items.append({
                "text": results["documents"][0][i],
                "score": round(score, 4),
                "metadata": results["metadatas"][0][i],
            })
        return items

    def delete_document(self, collection_name: str, document_id: str) -> int:
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(where={"document_id": document_id}, include=[])
        if results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Deleted {len(results['ids'])} chunks for doc {document_id}")
            return len(results["ids"])
        return 0

    def delete_collection(self, name: str) -> None:
        self.client.delete_collection(name)
        logger.info(f"Deleted collection '{name}'")

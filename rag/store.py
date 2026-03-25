"""ChromaDB vector store wrapper."""

import logging
import uuid
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
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        document_id: str | None = None,
    ) -> str:
        doc_id = document_id or str(uuid.uuid4())
        collection = self.get_or_create_collection(collection_name)

        ids = [f"{doc_id}_{i}" for i in range(len(texts))]
        for meta in metadatas:
            meta["document_id"] = doc_id
            meta["indexed_at"] = datetime.now(timezone.utc).isoformat()

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        logger.info(f"Added {len(texts)} chunks to collection '{collection_name}' (doc: {doc_id})")
        return doc_id

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
            score = 1.0 - distance  # cosine distance → similarity
            items.append({
                "text": results["documents"][0][i],
                "score": round(score, 4),
                "metadata": results["metadatas"][0][i],
            })

        return items

    def delete_document(self, collection_name: str, document_id: str) -> int:
        collection = self.get_or_create_collection(collection_name)
        # Find all chunks belonging to this document
        results = collection.get(
            where={"document_id": document_id},
            include=[],
        )
        if results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Deleted {len(results['ids'])} chunks for doc {document_id}")
            return len(results["ids"])
        return 0

    def list_documents(self, collection_name: str) -> list[dict]:
        collection = self.get_or_create_collection(collection_name)
        all_data = collection.get(include=["metadatas"])

        docs = {}
        for meta in all_data["metadatas"]:
            doc_id = meta.get("document_id", "unknown")
            if doc_id not in docs:
                docs[doc_id] = {
                    "id": doc_id,
                    "filename": meta.get("filename", "unknown"),
                    "collection": collection_name,
                    "chunk_count": 0,
                    "created_at": meta.get("indexed_at", ""),
                }
            docs[doc_id]["chunk_count"] += 1

        return list(docs.values())

    def list_collections(self) -> list[dict]:
        collections = self.client.list_collections()
        result = []
        for col in collections:
            count = col.count()
            # Estimate unique documents
            doc_ids = set()
            if count > 0:
                data = col.get(include=["metadatas"], limit=10000)
                doc_ids = {m.get("document_id") for m in data["metadatas"] if m.get("document_id")}
            result.append({
                "name": col.name,
                "description": col.metadata.get("description", ""),
                "document_count": len(doc_ids),
                "chunk_count": count,
            })
        return result

    def delete_collection(self, name: str) -> None:
        self.client.delete_collection(name)
        logger.info(f"Deleted collection '{name}'")

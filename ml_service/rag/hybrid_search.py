"""Hybrid search: Tantivy BM25 + semantic via Reciprocal Rank Fusion.

BM25 indexes are persistent on disk (one tantivy index per collection).
Supports true incremental add and delete — no full rebuild needed.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from pathlib import Path

import tantivy

from config import BM25_INDEX_DIR, BM25_WRITER_HEAP_SIZE

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY = 0.5  # seconds


class TantivyBM25Index:
    """Manages a single tantivy index for one collection.

    Thread-safety: all write operations are serialized via a single lock.
    Only one writer exists per index (tantivy requirement on Windows).
    """

    def __init__(self, index_dir: Path, heap_size: int = BM25_WRITER_HEAP_SIZE):
        self._index_dir = index_dir
        self._heap_size = heap_size
        self._lock = threading.Lock()

        schema_builder = tantivy.SchemaBuilder()
        schema_builder.add_text_field("chunk_id", stored=True, tokenizer_name="raw")
        schema_builder.add_text_field("body", stored=True)
        schema_builder.add_text_field("metadata", stored=True, tokenizer_name="raw")
        self._schema = schema_builder.build()

        self._index = self._open_or_create()
        self._writer: tantivy.IndexWriter | None = None
        self._ensure_writer()

    def _open_or_create(self) -> tantivy.Index:
        """Open existing index or create a new one, handling schema mismatches."""
        self._index_dir.mkdir(parents=True, exist_ok=True)
        try:
            return tantivy.Index(self._schema, path=str(self._index_dir))
        except Exception:
            logger.warning(
                "Schema mismatch for '%s' — deleting and recreating index",
                self._index_dir,
            )
            shutil.rmtree(self._index_dir, ignore_errors=True)
            self._index_dir.mkdir(parents=True, exist_ok=True)
            return tantivy.Index(self._schema, path=str(self._index_dir))

    def _ensure_writer(self) -> None:
        """Create a writer if one doesn't exist or the previous one is broken."""
        if self._writer is None:
            self._writer = self._index.writer(self._heap_size)

    def _reset_writer(self) -> None:
        """Discard the current writer and create a fresh one."""
        try:
            if self._writer is not None:
                del self._writer
        except Exception:
            pass
        self._writer = None
        # Small delay to let OS release file handles (Windows issue)
        time.sleep(0.1)
        self._writer = self._index.writer(self._heap_size)

    # ── Write operations ──────────────────────────────────────────────────

    def add(self, chunk_ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        """Add documents to the index (upsert — deletes existing IDs first)."""
        with self._lock:
            for attempt in range(_MAX_RETRIES):
                try:
                    self._ensure_writer()
                    # Delete existing docs with same IDs to avoid duplicates
                    for cid in chunk_ids:
                        self._writer.delete_documents("chunk_id", cid)
                    for cid, text, meta in zip(chunk_ids, texts, metadatas):
                        self._writer.add_document(tantivy.Document(
                            chunk_id=cid,
                            body=text,
                            metadata=json.dumps(meta, ensure_ascii=False),
                        ))
                    self._writer.commit()
                    self._index.reload()
                    return
                except Exception:
                    logger.warning(
                        "Tantivy write failed for '%s' (attempt %d/%d), resetting writer",
                        self._index_dir.name, attempt + 1, _MAX_RETRIES,
                    )
                    self._reset_writer()
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_RETRY_DELAY)
            # Final attempt failed — raise
            raise RuntimeError(f"Tantivy write failed after {_MAX_RETRIES} retries for '{self._index_dir.name}'")

    def delete(self, chunk_ids: list[str]) -> None:
        """Delete documents by chunk_id."""
        if not chunk_ids:
            return
        with self._lock:
            for attempt in range(_MAX_RETRIES):
                try:
                    self._ensure_writer()
                    for cid in chunk_ids:
                        self._writer.delete_documents("chunk_id", cid)
                    self._writer.commit()
                    self._index.reload()
                    return
                except Exception:
                    logger.warning(
                        "Tantivy delete failed for '%s' (attempt %d/%d), resetting writer",
                        self._index_dir.name, attempt + 1, _MAX_RETRIES,
                    )
                    self._reset_writer()
                    if attempt < _MAX_RETRIES - 1:
                        time.sleep(_RETRY_DELAY)
            raise RuntimeError(f"Tantivy delete failed after {_MAX_RETRIES} retries for '{self._index_dir.name}'")

    # ── Read operations ───────────────────────────────────────────────────

    def search(self, query: str, top_k: int) -> list[dict]:
        """Search the index and return top-k results."""
        self._index.reload()
        searcher = self._index.searcher()
        parsed_query = self._index.parse_query(query, ["body"])
        hits = searcher.search(parsed_query, limit=top_k).hits
        results = []
        for score, doc_address in hits:
            doc = searcher.doc(doc_address)
            results.append({
                "text": doc["body"][0],
                "score": round(float(score), 4),
                "metadata": json.loads(doc["metadata"][0]),
            })
        return results

    @property
    def doc_count(self) -> int:
        """Return the number of documents in the index."""
        self._index.reload()
        searcher = self._index.searcher()
        return searcher.num_docs

    @property
    def index_path(self) -> str:
        return str(self._index_dir)


class HybridSearcher:
    """Manages per-collection Tantivy BM25 indexes."""

    def __init__(self):
        self._indexes: dict[str, TantivyBM25Index] = {}
        self._lock = threading.Lock()

    def _get_index(self, collection_name: str) -> TantivyBM25Index:
        with self._lock:
            if collection_name not in self._indexes:
                index_dir = BM25_INDEX_DIR / collection_name
                self._indexes[collection_name] = TantivyBM25Index(index_dir)
            return self._indexes[collection_name]

    # ── Index management ──────────────────────────────────────────────────

    def add_chunks(
        self,
        collection_name: str,
        chunk_ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """Add chunks to the BM25 index for a collection."""
        idx = self._get_index(collection_name)
        idx.add(chunk_ids, texts, metadatas)
        logger.info("BM25 add %d chunks to '%s'", len(chunk_ids), collection_name)

    def mark_deleted(self, collection_name: str, chunk_ids: list[str]) -> None:
        """Delete chunks from the BM25 index for a collection."""
        idx = self._get_index(collection_name)
        idx.delete(chunk_ids)
        logger.info("BM25 delete %d chunks from '%s'", len(chunk_ids), collection_name)

    def invalidate(self, collection_name: str) -> None:
        """No-op — kept for backward compatibility."""
        pass

    def delete_collection(self, collection_name: str) -> None:
        """Delete the entire BM25 index for a collection."""
        with self._lock:
            self._indexes.pop(collection_name, None)
        index_dir = BM25_INDEX_DIR / collection_name
        if index_dir.exists():
            shutil.rmtree(index_dir, ignore_errors=True)
            logger.info("Deleted BM25 index for '%s'", collection_name)

    # ── Search ────────────────────────────────────────────────────────────

    def bm25_search(self, collection_name: str, query: str, top_k: int) -> list[dict]:
        """Return top-k chunks ranked by BM25 score."""
        idx = self._get_index(collection_name)
        return idx.search(query, top_k)

    def hybrid_search(
        self,
        collection_name: str,
        query: str,
        semantic_results: list[dict],
        top_k: int,
        k: int = 60,
    ) -> list[dict]:
        """Fuse semantic and BM25 results with Reciprocal Rank Fusion.

        RRF formula: score(d) = sum 1 / (k + rank_i(d))
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

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, dict]:
        """Return stats for all known collections."""
        with self._lock:
            names = list(self._indexes.keys())
        result = {}
        for name in names:
            idx = self._get_index(name)
            result[name] = {
                "doc_count": idx.doc_count,
                "index_path": idx.index_path,
            }
        return result

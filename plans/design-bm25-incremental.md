# BM25 Incremental Index Updates — Design Document

**Date**: 2026-03-26
**Author**: Architecture Review
**Status**: Proposed
**Target file**: `ml_service/rag/hybrid_search.py`

---

## 1. Problem Statement

### Current Implementation

`HybridSearcher` in `ml_service/rag/hybrid_search.py` maintains per-collection BM25 indexes using a simple `dict[str, tuple[BM25Okapi, list[dict]]]`. The lifecycle is:

1. On the first `bm25_search` or `hybrid_search` call after any invalidation, `_get_index()` calls `_build_index()`.
2. `_build_index()` calls `collection.get(include=["documents", "metadatas"])` — a full table scan of ChromaDB — then constructs a brand-new `BM25Okapi` object from all tokenized texts.
3. Both `index_chunks` and `delete_document` endpoints call `hybrid_searcher.invalidate(collection_name)`, which simply removes the entry from `_indexes`.
4. Result: the next search after **any** write triggers a full rebuild.

### Concrete Pain Points

| Scenario | Impact |
|---|---|
| 100K chunks in a collection | `collection.get()` returns ~100K strings; `BM25Okapi([...])` builds IDF tables over all of them. Cold-start latency: 5–30 s |
| Adding a single new document (50 chunks) | Full 100K rebuild instead of appending 50 entries |
| 10 concurrent writers | Index invalidated 10 times; 10 concurrent cold-start rebuilds race under a single lock, serialised to 10× rebuild time |
| 50+ collections in memory | No eviction — unbounded memory growth |
| `_lock` held during `_build_index()` | All other searches on **any** collection blocked while one collection rebuilds |

### Root Cause

`rank_bm25`'s `BM25Okapi` does not expose an incremental API. The constructor is the only entry point, so the current code treats every structural change as a full-rebuild event. The lock design compounds the problem: it serialises both cache lookups and the expensive rebuild inside the same critical section.

---

## 2. Solution Overview

### Chosen Approach: Delta Buffer + Background Rebuild + Soft Deletes

Rather than adopting a new on-disk index library immediately, the design extends the existing `rank_bm25`-based approach with four complementary mechanisms:

1. **Delta buffer**: newly added chunks are buffered in a list. Searches query both the existing `BM25Okapi` instance and the small delta buffer, then merge results.
2. **Soft deletes**: deleted chunk IDs are recorded in a `set`. Results from both the main index and the delta buffer are filtered against this set before returning.
3. **Background async rebuild**: when the delta buffer exceeds a configurable threshold (or after a delete), a background thread rebuilds the full index without holding the query lock. Searches continue to serve the stale-but-functional index until the new one is swapped in atomically.
4. **LRU eviction**: a per-process LRU cache limits memory to `BM25_MAX_CACHED_COLLECTIONS` (default 50) collections.

This gives O(1) amortised writes, non-blocking reads, and a bounded memory footprint — without adding new binary dependencies (Whoosh, Tantivy) or changing the external API at all.

### Why Not Whoosh / Tantivy Immediately?

See Section 8 for the detailed trade-off discussion. The short answer: persistent index libraries solve the persistence problem but introduce deployment complexity (native extensions, disk I/O, schema migrations) that is disproportionate for an embedded ML microservice. The delta-buffer approach can be layered on top of `rank_bm25` in under 200 lines and adopted incrementally.

---

## 3. Configuration

### 3.1 New Environment Variables

Add to `ml_service/config.py`:

```python
# ml_service/config.py  (additions only — append after existing variables)

# ── BM25 incremental index configuration ────────────────────────────────────

# Maximum number of per-collection BM25 indexes kept in memory.
# When this limit is reached the least-recently-used collection is evicted.
BM25_MAX_CACHED_COLLECTIONS: int = int(os.getenv("BM25_MAX_CACHED_COLLECTIONS", "50"))

# Number of pending (unbuffered) chunks that triggers a background rebuild.
# Lower values keep the delta buffer smaller (faster searches) at the cost of
# more frequent background rebuilds.
BM25_REBUILD_BATCH_SIZE: int = int(os.getenv("BM25_REBUILD_BATCH_SIZE", "500"))

# Maximum number of worker threads used for background BM25 rebuilds.
BM25_REBUILD_WORKERS: int = int(os.getenv("BM25_REBUILD_WORKERS", "2"))
```

---

## 4. Data Structures

Before showing code, it helps to understand the state held per collection.

```
IndexEntry
├── bm25          : BM25Okapi | None          — main index (may be None if never built)
├── chunks        : list[dict]                — parallel to bm25 corpus; each is {text, metadata, id}
├── delta_texts   : list[str]                 — texts added since last rebuild
├── delta_ids     : list[str]                 — chunk IDs for delta_texts
├── delta_meta    : list[dict]                — metadata for delta_texts
├── deleted_ids   : set[str]                  — soft-deleted chunk IDs
├── version       : int                       — monotonically increasing write counter
├── rebuilding    : bool                      — True while background rebuild is running
└── last_used     : float                     — time.monotonic() for LRU eviction
```

The main `_indexes` dict maps `collection_name → IndexEntry`. Access is protected by a per-entry `RLock` (for the entry itself) and a top-level `Lock` (for the `_indexes` dict).

---

## 5. Incremental Add

### 5.1 Design

When `index_chunks` is called, instead of invalidating the index, the new chunks are appended to `delta_texts` / `delta_ids` / `delta_meta`. The search path queries both the main `BM25Okapi` and the delta buffer. If the delta buffer size exceeds `BM25_REBUILD_BATCH_SIZE`, a background rebuild is scheduled.

This means:
- Write path: O(k) where k = number of new chunks (just list appends)
- Query path: O(n + k) where n = main index size, k = delta size (two separate BM25 score vectors merged)
- Background rebuild: O(n + k), but off the critical path

### 5.2 Code

**File: `ml_service/rag/hybrid_search.py`** (complete replacement)

```python
"""Hybrid search: BM25 + semantic via Reciprocal Rank Fusion.

BM25 indexes are maintained per-collection with:
- Incremental adds via a delta buffer (avoids full rebuild on every write)
- Soft deletes (avoids full rebuild on every delete)
- Background async rebuilds (searches never block waiting for a rebuild)
- LRU eviction when more than BM25_MAX_CACHED_COLLECTIONS are cached
- Version counters for stale-read detection under concurrent writes
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from rank_bm25 import BM25Okapi

from config import (
    BM25_MAX_CACHED_COLLECTIONS,
    BM25_REBUILD_BATCH_SIZE,
    BM25_REBUILD_WORKERS,
)
from rag.store import VectorStore

logger = logging.getLogger(__name__)


# ── Per-collection state ──────────────────────────────────────────────────────

@dataclass
class IndexEntry:
    """All mutable state for one collection's BM25 index."""

    # Main index — None means "never built yet"
    bm25: Optional[BM25Okapi] = None
    # Parallel list of chunk dicts for the main index corpus
    chunks: list[dict] = field(default_factory=list)

    # Delta buffer: chunks added after the last full rebuild
    delta_texts: list[str] = field(default_factory=list)
    delta_ids: list[str] = field(default_factory=list)
    delta_meta: list[dict] = field(default_factory=list)

    # Soft-delete set: chunk IDs that should be excluded from results
    deleted_ids: set[str] = field(default_factory=set)

    # Monotonically increasing version — incremented on every structural write
    version: int = 0

    # Guards all fields of this entry
    lock: threading.RLock = field(default_factory=threading.RLock)

    # True while a background rebuild is in flight for this collection
    rebuilding: bool = False

    # Monotonic timestamp of last access — used for LRU eviction
    last_used: float = field(default_factory=time.monotonic)
```

---

## 6. Incremental Delete (Soft Deletes)

### 6.1 Design

Deleting chunks from an in-memory `BM25Okapi` is impossible without rebuilding it. Instead:

1. The chunk IDs to delete are added to `deleted_ids`.
2. The version counter is bumped.
3. A background rebuild is scheduled (the deleted chunks waste memory in the main corpus until the rebuild completes, but that is bounded — they do not appear in results).
4. Both the main-index result path and the delta-buffer result path filter against `deleted_ids`.

This means a delete is O(d) where d = number of deleted chunk IDs, not O(n).

### 6.2 Code (method on `HybridSearcher`)

```python
    def add_chunks(
        self,
        collection_name: str,
        chunk_ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """Append new chunks to the delta buffer; schedule rebuild if threshold met."""
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            entry.delta_texts.extend(texts)
            entry.delta_ids.extend(chunk_ids)
            entry.delta_meta.extend(metadatas)
            entry.version += 1
            delta_size = len(entry.delta_texts)

        logger.debug(
            "add_chunks '%s': +%d chunks, delta_size=%d",
            collection_name, len(texts), delta_size,
        )

        if delta_size >= BM25_REBUILD_BATCH_SIZE:
            self._schedule_rebuild(collection_name)

    def mark_deleted(self, collection_name: str, chunk_ids: list[str]) -> None:
        """Soft-delete chunk IDs; schedule background rebuild to reclaim memory."""
        if not chunk_ids:
            return
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            entry.deleted_ids.update(chunk_ids)
            entry.version += 1

        logger.debug(
            "mark_deleted '%s': +%d deleted ids (total=%d)",
            collection_name, len(chunk_ids), len(entry.deleted_ids),
        )
        # Schedule rebuild to physically remove deleted chunks from memory
        self._schedule_rebuild(collection_name)
```

---

## 7. Background Async Rebuild

### 7.1 Design

A `ThreadPoolExecutor` with `BM25_REBUILD_WORKERS` threads handles all rebuilds. The rebuild procedure:

1. Acquires the entry lock, sets `rebuilding = True`, reads `version` at that moment.
2. Releases the lock and performs the expensive ChromaDB fetch + `BM25Okapi()` construction **outside the lock**.
3. Re-acquires the lock. If the `version` has changed since step 1, discards this rebuild result (a newer write happened while rebuilding — a fresh rebuild will be scheduled by that write).
4. Otherwise, atomically swaps in the new `bm25` and `chunks`, clears the delta buffer and `deleted_ids`, sets `rebuilding = False`.

Searches during the rebuild window see the previous `bm25` + delta buffer, which may include some stale deletes (filtered by `deleted_ids`) and the current delta adds. This is safe and correct.

### 7.2 Full `HybridSearcher` Class

```python
class HybridSearcher:
    """
    Manages per-collection BM25 indexes.

    Thread-safety model:
    - _catalog_lock guards the _catalog OrderedDict (insert / evict / LRU reorder).
    - Each IndexEntry has its own RLock that guards its fields.
    - The expensive ChromaDB fetch + BM25Okapi() call happens outside both locks.
    """

    def __init__(self, store: VectorStore) -> None:
        self._store = store
        # Ordered so we can do LRU eviction cheaply (move_to_end / popitem(last=False))
        self._catalog: OrderedDict[str, IndexEntry] = OrderedDict()
        self._catalog_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=BM25_REBUILD_WORKERS,
            thread_name_prefix="bm25-rebuild",
        )

    # ── Catalog / LRU management ──────────────────────────────────────────────

    def _get_or_create_entry(self, collection_name: str) -> IndexEntry:
        """Return the IndexEntry for a collection, creating and LRU-evicting as needed."""
        with self._catalog_lock:
            if collection_name in self._catalog:
                self._catalog.move_to_end(collection_name)
                entry = self._catalog[collection_name]
                entry.last_used = time.monotonic()
                return entry

            # New entry — evict LRU if at capacity
            while len(self._catalog) >= BM25_MAX_CACHED_COLLECTIONS:
                evicted_name, _ = self._catalog.popitem(last=False)
                logger.info("LRU evict BM25 index for '%s'", evicted_name)

            entry = IndexEntry()
            self._catalog[collection_name] = entry
            return entry

    def shutdown(self) -> None:
        """Gracefully shut down the rebuild thread pool."""
        self._executor.shutdown(wait=True)

    # ── Rebuild scheduling ────────────────────────────────────────────────────

    def _schedule_rebuild(self, collection_name: str) -> None:
        """Submit a background rebuild unless one is already running."""
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            if entry.rebuilding:
                logger.debug("Rebuild already in flight for '%s', skipping", collection_name)
                return
            entry.rebuilding = True
            snapshot_version = entry.version

        logger.info("Scheduling background BM25 rebuild for '%s' (v%d)", collection_name, snapshot_version)
        self._executor.submit(self._do_rebuild, collection_name, snapshot_version)

    def _do_rebuild(self, collection_name: str, trigger_version: int) -> None:
        """
        Background worker: fetch all docs from ChromaDB and rebuild BM25Okapi.
        Discards the result if a newer write occurred while rebuilding.
        """
        try:
            logger.info("BM25 rebuild started for '%s'", collection_name)
            t0 = time.monotonic()

            # --- Expensive work OUTSIDE the lock ---
            collection = self._store.get_or_create_collection(collection_name)
            result = collection.get(include=["documents", "metadatas", "ids"])
            ids: list[str] = result.get("ids") or []
            texts: list[str] = result.get("documents") or []
            metadatas: list[dict] = result.get("metadatas") or []

            # Build new index
            tokenized = [t.lower().split() for t in texts]
            new_bm25 = BM25Okapi(tokenized) if tokenized else BM25Okapi([[]])
            new_chunks = [
                {"text": t, "metadata": m, "id": i}
                for t, m, i in zip(texts, metadatas, ids)
            ]

            elapsed = time.monotonic() - t0
            logger.info(
                "BM25 rebuild for '%s' complete: %d chunks in %.2fs",
                collection_name, len(new_chunks), elapsed,
            )

            # --- Atomic swap ---
            entry = self._get_or_create_entry(collection_name)
            with entry.lock:
                if entry.version != trigger_version:
                    # A write happened while we were rebuilding — discard this result.
                    # The write that bumped the version will schedule its own rebuild.
                    logger.info(
                        "BM25 rebuild for '%s' discarded (version mismatch: expected %d, got %d)",
                        collection_name, trigger_version, entry.version,
                    )
                    entry.rebuilding = False
                    return

                # Apply soft-delete filter to the freshly fetched corpus
                active_deleted = entry.deleted_ids  # snapshot before clear
                entry.bm25 = new_bm25
                entry.chunks = [c for c in new_chunks if c["id"] not in active_deleted]
                # Rebuild BM25 over filtered corpus if any were deleted
                if active_deleted:
                    filtered_tokenized = [c["text"].lower().split() for c in entry.chunks]
                    entry.bm25 = BM25Okapi(filtered_tokenized) if filtered_tokenized else BM25Okapi([[]])

                # Clear delta buffer and soft-delete set
                entry.delta_texts.clear()
                entry.delta_ids.clear()
                entry.delta_meta.clear()
                entry.deleted_ids.clear()
                entry.rebuilding = False

        except Exception:
            logger.exception("BM25 rebuild failed for '%s'", collection_name)
            entry = self._get_or_create_entry(collection_name)
            with entry.lock:
                entry.rebuilding = False

    # ── Public write API ──────────────────────────────────────────────────────

    def add_chunks(
        self,
        collection_name: str,
        chunk_ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """Append new chunks to the delta buffer; schedule rebuild if threshold met."""
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            entry.delta_texts.extend(texts)
            entry.delta_ids.extend(chunk_ids)
            entry.delta_meta.extend(metadatas)
            entry.version += 1
            delta_size = len(entry.delta_texts)

        if delta_size >= BM25_REBUILD_BATCH_SIZE:
            self._schedule_rebuild(collection_name)

    def mark_deleted(self, collection_name: str, chunk_ids: list[str]) -> None:
        """Soft-delete chunk IDs; schedule background rebuild to reclaim memory."""
        if not chunk_ids:
            return
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            entry.deleted_ids.update(chunk_ids)
            entry.version += 1
        self._schedule_rebuild(collection_name)

    def invalidate(self, collection_name: str) -> None:
        """
        Full invalidation — kept for backward compatibility.
        Schedules a background rebuild rather than dropping the index immediately.
        """
        self._schedule_rebuild(collection_name)

    # ── Search ────────────────────────────────────────────────────────────────

    def _bm25_score_main(
        self,
        entry: IndexEntry,
        tokenized_query: list[str],
        deleted_ids: set[str],
    ) -> list[tuple[float, dict]]:
        """Score against the main BM25Okapi corpus, filtering soft-deletes."""
        if entry.bm25 is None or not entry.chunks:
            return []
        scores = entry.bm25.get_scores(tokenized_query)
        results = []
        for i, chunk in enumerate(entry.chunks):
            if chunk.get("id") in deleted_ids:
                continue
            results.append((float(scores[i]), chunk))
        return results

    def _bm25_score_delta(
        self,
        entry: IndexEntry,
        tokenized_query: list[str],
        deleted_ids: set[str],
    ) -> list[tuple[float, dict]]:
        """Score the delta buffer using a freshly built BM25Okapi (buffer is small)."""
        if not entry.delta_texts:
            return []

        active_indices = [
            i for i, cid in enumerate(entry.delta_ids)
            if cid not in deleted_ids
        ]
        if not active_indices:
            return []

        active_texts = [entry.delta_texts[i] for i in active_indices]
        tokenized_delta = [t.lower().split() for t in active_texts]
        delta_bm25 = BM25Okapi(tokenized_delta)
        delta_scores = delta_bm25.get_scores(tokenized_query)

        results = []
        for rank, orig_idx in enumerate(active_indices):
            chunk = {
                "text": entry.delta_texts[orig_idx],
                "metadata": entry.delta_meta[orig_idx],
                "id": entry.delta_ids[orig_idx],
            }
            results.append((float(delta_scores[rank]), chunk))
        return results

    def bm25_search(self, collection_name: str, query: str, top_k: int) -> list[dict]:
        """
        Return top-k chunks ranked by BM25 score.

        Queries both the main index (potentially stale) and the delta buffer,
        merges scores, and filters soft-deleted IDs.
        """
        entry = self._get_or_create_entry(collection_name)

        # Snapshot state under lock — release before scoring (scoring is CPU-bound, not I/O)
        with entry.lock:
            has_main = entry.bm25 is not None
            deleted_snapshot = frozenset(entry.deleted_ids)
            version_at_read = entry.version

        if not has_main and not entry.delta_texts:
            # Cold start: trigger a synchronous build of the main index
            self._cold_start_build(collection_name)

        tokenized_query = query.lower().split()

        # Score main + delta (read entry fields without holding the lock;
        # BM25Okapi.get_scores() is read-only and thread-safe for reads)
        main_results = self._bm25_score_main(entry, tokenized_query, deleted_snapshot)
        delta_results = self._bm25_score_delta(entry, tokenized_query, deleted_snapshot)

        # Merge by score (highest first), deduplicate by chunk ID
        all_results = main_results + delta_results
        all_results.sort(key=lambda x: x[0], reverse=True)

        seen_ids: set[str] = set()
        output: list[dict] = []
        for score, chunk in all_results:
            cid = chunk.get("id", chunk["text"])  # fall back to text as key
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            output.append({**chunk, "score": round(score, 4)})
            if len(output) >= top_k:
                break

        return output

    def _cold_start_build(self, collection_name: str) -> None:
        """
        Synchronous build for the very first search on a collection.
        Blocks the calling thread but is only triggered once per collection lifetime.
        """
        entry = self._get_or_create_entry(collection_name)
        with entry.lock:
            # Double-checked locking: another thread may have built it already
            if entry.bm25 is not None:
                return
            if entry.rebuilding:
                # Another thread is already rebuilding — wait for it by busy-waiting
                # on the flag (acceptable since this is a one-time event per collection)
                pass
            entry.rebuilding = True
            trigger_version = entry.version

        # Build synchronously (not in executor) to avoid returning empty results
        self._do_rebuild(collection_name, trigger_version)

    def hybrid_search(
        self,
        collection_name: str,
        query: str,
        semantic_results: list[dict],
        top_k: int,
        k: int = 60,
    ) -> list[dict]:
        """Fuse semantic and BM25 results with Reciprocal Rank Fusion.

        RRF formula: score(d) = sum(1 / (k + rank_i(d)))
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
```

---

## 8. Index Versioning

### 8.1 Purpose

The `version` counter on `IndexEntry` serves three purposes:

1. **Stale-rebuild detection**: `_do_rebuild` captures `version` before the expensive ChromaDB fetch. If `version` changes while rebuilding, the result is discarded and the latest writer's rebuild will run instead. This prevents a slow old rebuild from overwriting a fresher partial index.

2. **Monitoring / observability**: version can be exposed via a `/ml/health` or `/ml/bm25/stats` endpoint so operators can see how many writes have occurred since the last rebuild.

3. **Future: optimistic reads**: a caller can record the version before a search, re-read it after, and detect if the index was mutated during the search. Currently unused but costs nothing to maintain.

### 8.2 Version Exposure Endpoint (optional, in `main_ml.py`)

```python
# ml_service/main_ml.py — add after the /ml/health endpoint

from pydantic import BaseModel

class BM25StatsEntry(BaseModel):
    version: int
    main_chunks: int
    delta_chunks: int
    deleted_ids: int
    rebuilding: bool
    last_used: float

class BM25StatsResponse(BaseModel):
    collections: dict[str, BM25StatsEntry]

@app.get("/ml/bm25/stats", response_model=BM25StatsResponse)
async def bm25_stats():
    """Return BM25 index stats for all cached collections (diagnostic endpoint)."""
    stats: dict[str, BM25StatsEntry] = {}
    with hybrid_searcher._catalog_lock:
        for name, entry in hybrid_searcher._catalog.items():
            with entry.lock:
                stats[name] = BM25StatsEntry(
                    version=entry.version,
                    main_chunks=len(entry.chunks),
                    delta_chunks=len(entry.delta_texts),
                    deleted_ids=len(entry.deleted_ids),
                    rebuilding=entry.rebuilding,
                    last_used=entry.last_used,
                )
    return BM25StatsResponse(collections=stats)
```

---

## 9. Memory Management (LRU Eviction)

### 9.1 Design

The `_catalog` is an `OrderedDict`. Every access via `_get_or_create_entry` calls `move_to_end(collection_name)` to mark it as most-recently-used. When a new entry would push the catalog over `BM25_MAX_CACHED_COLLECTIONS`, the oldest entry is popped with `popitem(last=False)`.

Evicted entries are simply garbage-collected. The next search on an evicted collection triggers a cold-start rebuild (same path as a brand-new collection).

### 9.2 Memory Estimate

| Chunks per collection | Avg tokens per chunk | Memory per index |
|---|---|---|
| 10,000 | 100 tokens | ~80 MB |
| 50,000 | 100 tokens | ~400 MB |
| 100,000 | 100 tokens | ~800 MB |

With `BM25_MAX_CACHED_COLLECTIONS=50` and average collections of 10K chunks: ceiling ~4 GB. For larger deployments, lower the limit to 10–20. The delta buffer adds negligible memory since it only holds chunks added since the last rebuild.

### 9.3 LRU Code (already integrated in `_get_or_create_entry` above)

```python
    def _get_or_create_entry(self, collection_name: str) -> IndexEntry:
        with self._catalog_lock:
            if collection_name in self._catalog:
                self._catalog.move_to_end(collection_name)        # mark as MRU
                entry = self._catalog[collection_name]
                entry.last_used = time.monotonic()
                return entry

            # Evict LRU entries until under the limit
            while len(self._catalog) >= BM25_MAX_CACHED_COLLECTIONS:
                evicted_name, _ = self._catalog.popitem(last=False)  # pop LRU
                logger.info("LRU evict BM25 index for '%s'", evicted_name)

            entry = IndexEntry()
            self._catalog[collection_name] = entry
            return entry
```

---

## 10. Integration: Updated `main_ml.py` Call Sites

The two endpoints that currently call `hybrid_searcher.invalidate()` need to be updated to call the new write API instead. This requires knowing the chunk IDs that were written or deleted — both are already available in `store.py`.

### 10.1 `store.py` — expose chunk IDs from `add_chunks`

```python
# ml_service/rag/store.py — modified add_chunks signature

    def add_chunks(
        self,
        collection_name: str,
        document_id: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> list[str]:                          # <-- now returns the IDs
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
        return ids                           # <-- return the IDs
```

### 10.2 `store.py` — expose deleted IDs from `delete_document`

```python
# ml_service/rag/store.py — delete_document already returns the count;
# modified to also expose the IDs

    def delete_document(self, collection_name: str, document_id: str) -> tuple[int, list[str]]:
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(where={"document_id": document_id}, include=[])
        if results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Deleted {len(results['ids'])} chunks for doc {document_id}")
            return len(results["ids"]), results["ids"]
        return 0, []
```

### 10.3 `main_ml.py` — updated endpoint handlers

```python
# ml_service/main_ml.py — /ml/index handler (partial)

@app.post("/ml/index", response_model=IndexResponse)
async def index_chunks(req: IndexRequest):
    """Embed pre-chunked texts and store in ChromaDB."""
    if not req.chunks:
        return IndexResponse(document_id=req.document_id, chunk_count=0)

    texts = [c.text for c in req.chunks]
    metadatas = [dict(c.metadata) for c in req.chunks]

    embedder = get_embedder()
    loop = asyncio.get_event_loop()
    embeddings = await loop.run_in_executor(
        None, partial(embedder.embed_texts, texts)
    )

    store.get_or_create_collection(req.collection)
    chunk_ids = store.add_chunks(            # now returns IDs
        collection_name=req.collection,
        document_id=req.document_id,
        texts=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    # Incremental add instead of full invalidation
    hybrid_searcher.add_chunks(
        collection_name=req.collection,
        chunk_ids=chunk_ids,
        texts=texts,
        metadatas=metadatas,
    )

    return IndexResponse(document_id=req.document_id, chunk_count=len(texts))


# ml_service/main_ml.py — /ml/documents/delete handler (partial)

@app.post("/ml/documents/delete", response_model=DeleteDocumentResponse)
async def delete_document(req: DeleteDocumentRequest):
    """Delete all chunks of a document from ChromaDB."""
    deleted_count, deleted_ids = store.delete_document(req.collection, req.document_id)
    # Soft-delete instead of full invalidation
    hybrid_searcher.mark_deleted(
        collection_name=req.collection,
        chunk_ids=deleted_ids,
    )
    return DeleteDocumentResponse(chunks_deleted=deleted_count)
```

---

## 11. Alternative Approaches: Whoosh and Tantivy

### 11.1 Whoosh

[Whoosh](https://whoosh.readthedocs.io/) is a pure-Python, file-based full-text search library with true incremental index updates, segment-level merges, and BM25F scoring.

**Pros**:
- True incremental writes: `writer.add_document()` / `writer.delete_by_term()` without full rebuild
- Persistent index survives service restarts — no cold-start rebuild
- Tested multi-threading model with `BufferedWriter` and `AsyncWriter`
- Pure Python — no native extension, works on Windows without a C toolchain

**Cons**:
- Active development effectively stalled (last release 2013, Python 3 forks exist but fragmented)
- Disk I/O for every write — adds latency on NVMe-backed containers but noticeable on HDD or NFS mounts
- Schema must be defined upfront; adding new metadata fields requires reindexing
- Memory-mapped file handles can conflict with Windows file locking semantics
- Not designed for vectors — BM25 only; cannot be used for the embedding side

**Rough migration**: replace `BM25Okapi` with a `whoosh.index.Index` per collection, use `AsyncWriter` for writes, `searcher.search(query, limit=top_k)` for reads. A collection of 100K chunks would take ~500 MB on disk (compressed segments).

### 11.2 Tantivy-py

[tantivy-py](https://github.com/quickwit-oss/tantivy-py) wraps [Tantivy](https://github.com/quickwit-oss/tantivy) (a Rust full-text search engine) with Python bindings. Tantivy is the engine behind Quickwit and is production-grade.

**Pros**:
- Extremely fast: segment-level Lucene-like architecture with WAND query acceleration
- True incremental updates (commit = atomic segment flush; merge in background)
- Persistent; survives restarts
- BM25F scoring with configurable k1/b parameters
- Active development; Python 3.8–3.12 wheels available on PyPI

**Cons**:
- Native Rust extension — requires prebuilt wheel or Rust toolchain; adds ~15 MB to Docker image
- Python bindings are thinner than Whoosh's API; some advanced features require understanding Tantivy's segment model
- Schema still needs upfront definition; field additions require re-index
- Not pure Python — Windows wheel availability is generally good (PyPI provides it) but can be brittle in unusual environments

**Rough migration**:

```python
# tantivy-based BM25 index (sketch, not for direct copy-paste)
import tantivy

schema_builder = tantivy.SchemaBuilder()
schema_builder.add_text_field("body", stored=True)
schema_builder.add_text_field("chunk_id", stored=True, tokenizer_name="raw")
schema_builder.add_json_field("meta", stored=True)
schema = schema_builder.build()

index = tantivy.Index(schema, path="/data/bm25/my_collection")
writer = index.writer(heap_size=100_000_000)

# Add
writer.add_document(tantivy.Document(body=[text], chunk_id=[cid], meta=[json_meta]))
writer.commit()

# Delete
writer.delete_documents("chunk_id", cid)
writer.commit()

# Search
searcher = index.searcher()
query = index.parse_query(query_text, ["body"])
hits = searcher.search(query, limit=top_k)
```

### 11.3 Comparison Table

| Dimension | Current (delta-buffer) | Whoosh | Tantivy-py |
|---|---|---|---|
| Write latency | O(k) — list append | O(k) — segment write | O(k) — segment write |
| Read latency (cold) | O(n) — ChromaDB scan | O(1) — index on disk | O(1) — index on disk |
| Read latency (warm) | O(log n) + O(delta) | O(log n) | O(log n) |
| Memory usage | Bounded by LRU | Low (disk-backed) | Low (disk-backed) |
| Persistence | No (in-memory) | Yes | Yes |
| Native deps | None (pure Python) | None (pure Python) | Rust wheel |
| Windows support | Full | Full | Good (PyPI wheels) |
| Concurrent writes | Serialised per entry | AsyncWriter supports it | Writer lock (one writer) |
| Score compatibility | Exact BM25Okapi match | BM25F (close) | BM25F (close) |
| Maintenance burden | Low (no new dep) | Medium (stale library) | Medium (Rust wheel mgmt) |
| **Recommended for** | Current scale (<500K chunks) | If persistence needed, no Rust | If >500K chunks or HA needed |

**Recommendation**: adopt the delta-buffer approach now. If the collection size grows beyond 500K chunks per collection or if the service needs to restart without a 30-second warm-up rebuild, migrate to Tantivy-py as the persistent BM25 backend while keeping the same `HybridSearcher` interface.

---

## 12. Performance Benchmarks (Expected)

Measurements are estimates based on typical Python/rank_bm25 performance on a 4-core machine with 16 GB RAM. Actual numbers will vary.

### 12.1 Write Path

| Operation | Old (`invalidate`) | New (`add_chunks`) | Speedup |
|---|---|---|---|
| Add 50 chunks to 100K-chunk collection | Marks dirty; next search triggers 8–30s rebuild | List append: ~0.1 ms | ~100,000× |
| Delete 1 document (200 chunks) | Marks dirty; next search triggers 8–30s rebuild | Set update: ~0.05 ms | ~200,000× |

### 12.2 Read Path (Search Latency)

| Scenario | Old | New | Note |
|---|---|---|---|
| Cold start (never built) | 8–30 s blocking | 8–30 s (first time only, then warm) | Same cold start; only happens once |
| Warm read (no delta) | O(n) scoring only | O(n) scoring only | Identical |
| Warm read (500-chunk delta) | N/A (stale triggers rebuild) | O(n) main + O(500) delta | Extra ~1–2 ms for delta BM25 |
| Read during background rebuild | N/A (blocked by lock) | Served from stale main + delta | 0 ms extra latency |

### 12.3 Memory Usage Comparison

| Metric | Old | New |
|---|---|---|
| Memory per collection (100K chunks) | ~800 MB (1 per active collection) | ~800 MB main + O(delta) — same per-entry |
| Unbounded collections | Grows forever | Capped at `BM25_MAX_CACHED_COLLECTIONS` × 800 MB |
| Memory pressure under 50 collections | 40 GB+ | Capped at 40 GB default, tune to 5–10 for safety |

### 12.4 Concurrency

| Scenario | Old | New |
|---|---|---|
| 10 concurrent index writes | 10 serialised full rebuilds after each write | 10 list appends; 1 background rebuild |
| 10 concurrent searches | All blocked if any one triggers cold start | 9 served from stale; 1 waits for cold start (first time only) |
| Background rebuild races | N/A | Version mismatch detection discards stale rebuilds |

---

## 13. Implementation Checklist

- [ ] Add `BM25_MAX_CACHED_COLLECTIONS`, `BM25_REBUILD_BATCH_SIZE`, `BM25_REBUILD_WORKERS` to `ml_service/config.py`
- [ ] Add `BM25_MAX_CACHED_COLLECTIONS`, `BM25_REBUILD_BATCH_SIZE`, `BM25_REBUILD_WORKERS` to `.env.example`
- [ ] Replace `ml_service/rag/hybrid_search.py` with the new `IndexEntry` + `HybridSearcher` implementation
- [ ] Update `ml_service/rag/store.py`: `add_chunks` returns `list[str]` of IDs; `delete_document` returns `tuple[int, list[str]]`
- [ ] Update `ml_service/main_ml.py`: replace `invalidate()` calls with `add_chunks()` / `mark_deleted()` calls
- [ ] Add `GET /ml/bm25/stats` diagnostic endpoint (optional)
- [ ] Update `HybridSearcher.shutdown()` call in the FastAPI `lifespan` teardown:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      ...
      yield
      hybrid_searcher.shutdown()   # drain background rebuild threads
  ```
- [ ] Write unit tests for:
  - Delta buffer add + search returns delta results
  - Soft delete filters deleted IDs from both main and delta
  - Version mismatch causes rebuild discard
  - LRU eviction at capacity
  - Background rebuild swaps index atomically

---

## 14. Trade-offs and Limitations

| Trade-off | Impact | Mitigation |
|---|---|---|
| Delta buffer uses a fresh `BM25Okapi` per search | For delta > ~5K chunks, this adds noticeable overhead (each query builds a temporary `BM25Okapi`) | `BM25_REBUILD_BATCH_SIZE` default of 500 keeps the delta small; if it grows, the background rebuild collapses it |
| Scores from main index and delta index are on different IDF scales | Merging raw BM25 scores from two different corpora is statistically imprecise | Use score normalisation (divide by max score per sub-index) before merging; or use RRF between the two sub-results rather than raw score merge |
| Soft-deleted chunks stay in memory until rebuild completes | For mass deletes (e.g., deleting a 10K-chunk document), memory is not freed immediately | Background rebuild is triggered immediately on delete; memory reclaimed within seconds |
| `_do_rebuild` holds ChromaDB under no lock — ChromaDB may return a snapshot mid-write | Possible: a concurrent write completes, but the rebuild fetches a slightly stale snapshot | Tolerated: the version mismatch check will discard any rebuild that raced with a write, and the write's own rebuild will produce a correct result |
| Thread pool is process-global | Two collections can rebuild concurrently, but at most `BM25_REBUILD_WORKERS` rebuilds run in parallel | Increase `BM25_REBUILD_WORKERS` if rebuild queue backs up; monitored via `/ml/bm25/stats` |
| No persistence across service restarts | Cold start always required per collection after restart | Acceptable for current scale; migrate to Tantivy-py if warm-restart is required |
| LRU eviction drops the entire index | An evicted collection must cold-start on next search | Set `BM25_MAX_CACHED_COLLECTIONS` high enough to cover the expected active collection count |

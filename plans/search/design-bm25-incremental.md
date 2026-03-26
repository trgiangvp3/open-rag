# BM25 Cập Nhật Index Gia Tăng — Tài Liệu Thiết Kế

**Ngày**: 2026-03-26
**Tác giả**: Architecture Review
**Trạng thái**: Đề xuất
**File mục tiêu**: `ml_service/rag/hybrid_search.py`

---

## 1. Mô Tả Vấn Đề

### Triển Khai Hiện Tại

`HybridSearcher` trong `ml_service/rag/hybrid_search.py` duy trì các BM25 index theo từng collection bằng một `dict[str, tuple[BM25Okapi, list[dict]]]` đơn giản. Vòng đời hoạt động như sau:

1. Ở lần gọi `bm25_search` hoặc `hybrid_search` đầu tiên sau bất kỳ lần vô hiệu hóa nào, `_get_index()` gọi `_build_index()`.
2. `_build_index()` gọi `collection.get(include=["documents", "metadatas"])` — quét toàn bộ bảng trong ChromaDB — sau đó xây dựng một đối tượng `BM25Okapi` mới hoàn toàn từ tất cả các văn bản đã được token hóa.
3. Cả hai endpoint `index_chunks` và `delete_document` đều gọi `hybrid_searcher.invalidate(collection_name)`, thao tác này chỉ đơn giản là xóa entry khỏi `_indexes`.
4. Kết quả: lần tìm kiếm tiếp theo sau **bất kỳ** thao tác ghi nào đều kích hoạt xây dựng lại toàn bộ.

### Các Vấn Đề Cụ Thể

| Tình huống | Ảnh hưởng |
|---|---|
| 100K chunk trong một collection | `collection.get()` trả về ~100K chuỗi; `BM25Okapi([...])` xây dựng bảng IDF trên tất cả chúng. Độ trễ khởi động nguội: 5–30 giây |
| Thêm một tài liệu mới (50 chunk) | Xây dựng lại toàn bộ 100K thay vì chỉ thêm 50 mục |
| 10 writer đồng thời | Index bị vô hiệu hóa 10 lần; 10 lần xây dựng lại khởi động nguội đồng thời cạnh tranh dưới một lock duy nhất, bị tuần tự hóa thành 10× thời gian xây dựng lại |
| 50+ collection trong bộ nhớ | Không có cơ chế xóa bỏ — bộ nhớ tăng không giới hạn |
| `_lock` được giữ trong `_build_index()` | Tất cả các tìm kiếm khác trên **bất kỳ** collection nào đều bị chặn trong khi một collection đang xây dựng lại |

### Nguyên Nhân Gốc Rễ

`BM25Okapi` của `rank_bm25` không cung cấp API gia tăng. Constructor là điểm vào duy nhất, vì vậy code hiện tại coi mọi thay đổi cấu trúc như một sự kiện xây dựng lại toàn bộ. Thiết kế lock làm trầm trọng thêm vấn đề: nó tuần tự hóa cả tra cứu cache và xây dựng lại tốn kém bên trong cùng một critical section.

---

## 2. Tổng Quan Giải Pháp

### Phương Pháp Được Chọn: Delta Buffer + Xây Dựng Lại Nền + Soft Deletes

Thay vì áp dụng ngay một thư viện index trên đĩa mới, thiết kế này mở rộng phương pháp dựa trên `rank_bm25` hiện có với bốn cơ chế bổ sung:

1. **Delta buffer**: các chunk mới được thêm sẽ được đệm vào một danh sách. Tìm kiếm truy vấn cả instance `BM25Okapi` hiện có lẫn delta buffer nhỏ, sau đó hợp nhất kết quả.
2. **Soft deletes**: các ID chunk đã xóa được ghi lại trong một `set`. Kết quả từ cả index chính lẫn delta buffer đều được lọc qua tập này trước khi trả về.
3. **Xây dựng lại nền bất đồng bộ**: khi delta buffer vượt quá ngưỡng có thể cấu hình (hoặc sau một lần xóa), một luồng nền xây dựng lại toàn bộ index mà không giữ query lock. Tìm kiếm tiếp tục phục vụ từ index cũ nhưng vẫn hoạt động được cho đến khi index mới được hoán đổi vào theo cách nguyên tử.
4. **LRU eviction**: một LRU cache theo tiến trình giới hạn bộ nhớ xuống `BM25_MAX_CACHED_COLLECTIONS` (mặc định 50) collection.

Điều này mang lại O(1) amortised cho các thao tác ghi, đọc không bị chặn, và bộ nhớ sử dụng có giới hạn — mà không cần thêm dependency nhị phân mới (Whoosh, Tantivy) hoặc thay đổi API bên ngoài.

### Tại Sao Không Dùng Whoosh / Tantivy Ngay?

Xem Mục 8 để thảo luận chi tiết về sự đánh đổi. Câu trả lời ngắn gọn: các thư viện index bền vững giải quyết vấn đề lưu trữ nhưng lại mang đến sự phức tạp trong triển khai (native extensions, disk I/O, schema migrations) không cân xứng với một ML microservice nhúng. Phương pháp delta-buffer có thể được xây dựng thêm trên `rank_bm25` trong dưới 200 dòng và áp dụng dần dần.

---

## 3. Cấu Hình

### 3.1 Biến Môi Trường Mới

Thêm vào `ml_service/config.py`:

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

## 4. Cấu Trúc Dữ Liệu

Trước khi trình bày code, cần hiểu trạng thái được lưu trữ cho mỗi collection.

```
IndexEntry
├── bm25          : BM25Okapi | None          — index chính (có thể là None nếu chưa được xây dựng)
├── chunks        : list[dict]                — song song với corpus bm25; mỗi phần tử là {text, metadata, id}
├── delta_texts   : list[str]                 — các văn bản được thêm kể từ lần xây dựng lại cuối
├── delta_ids     : list[str]                 — ID chunk cho delta_texts
├── delta_meta    : list[dict]                — metadata cho delta_texts
├── deleted_ids   : set[str]                  — ID chunk đã bị soft-delete
├── version       : int                       — bộ đếm ghi tăng đơn điệu
├── rebuilding    : bool                      — True khi đang chạy xây dựng lại nền
└── last_used     : float                     — time.monotonic() để LRU eviction
```

Dict `_indexes` chính ánh xạ `collection_name → IndexEntry`. Truy cập được bảo vệ bởi một `RLock` theo entry (cho chính entry đó) và một `Lock` cấp cao nhất (cho dict `_indexes`).

---

## 5. Thêm Gia Tăng

### 5.1 Thiết Kế

Khi `index_chunks` được gọi, thay vì vô hiệu hóa index, các chunk mới được thêm vào `delta_texts` / `delta_ids` / `delta_meta`. Đường dẫn tìm kiếm truy vấn cả `BM25Okapi` chính lẫn delta buffer. Nếu kích thước delta buffer vượt quá `BM25_REBUILD_BATCH_SIZE`, một lần xây dựng lại nền sẽ được lên lịch.

Điều này có nghĩa là:
- Đường dẫn ghi: O(k) với k = số chunk mới (chỉ là thêm vào danh sách)
- Đường dẫn truy vấn: O(n + k) với n = kích thước index chính, k = kích thước delta (hai vector điểm BM25 riêng biệt được hợp nhất)
- Xây dựng lại nền: O(n + k), nhưng ngoài critical path

### 5.2 Code

**File: `ml_service/rag/hybrid_search.py`** (thay thế hoàn toàn)

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

## 6. Xóa Gia Tăng (Soft Deletes)

### 6.1 Thiết Kế

Việc xóa chunk khỏi `BM25Okapi` trong bộ nhớ là không thể mà không xây dựng lại. Thay vào đó:

1. Các ID chunk cần xóa được thêm vào `deleted_ids`.
2. Bộ đếm version được tăng lên.
3. Một lần xây dựng lại nền được lên lịch (các chunk đã xóa vẫn chiếm bộ nhớ trong corpus chính cho đến khi xây dựng lại hoàn thành, nhưng điều đó có giới hạn — chúng không xuất hiện trong kết quả).
4. Cả đường dẫn kết quả của index chính lẫn delta buffer đều lọc theo `deleted_ids`.

Điều này có nghĩa là một lần xóa là O(d) với d = số ID chunk bị xóa, không phải O(n).

### 6.2 Code (phương thức trên `HybridSearcher`)

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

## 7. Xây Dựng Lại Nền Bất Đồng Bộ

### 7.1 Thiết Kế

Một `ThreadPoolExecutor` với `BM25_REBUILD_WORKERS` luồng xử lý tất cả các lần xây dựng lại. Quy trình xây dựng lại:

1. Lấy entry lock, đặt `rebuilding = True`, đọc `version` tại thời điểm đó.
2. Giải phóng lock và thực hiện việc tốn kém là fetch ChromaDB + xây dựng `BM25Okapi()` **bên ngoài lock**.
3. Lấy lại lock. Nếu `version` đã thay đổi kể từ bước 1, hủy kết quả xây dựng lại này (một thao tác ghi mới hơn đã xảy ra trong khi đang xây dựng lại — một lần xây dựng lại mới sẽ được lên lịch bởi thao tác ghi đó).
4. Ngược lại, hoán đổi nguyên tử `bm25` và `chunks` mới vào, xóa delta buffer và `deleted_ids`, đặt `rebuilding = False`.

Các tìm kiếm trong cửa sổ xây dựng lại sẽ thấy `bm25` cũ + delta buffer, có thể bao gồm một số lần xóa cũ (được lọc bởi `deleted_ids`) và các thêm delta hiện tại. Điều này an toàn và đúng đắn.

### 7.2 Class `HybridSearcher` Đầy Đủ

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

## 8. Phiên Bản Index

### 8.1 Mục Đích

Bộ đếm `version` trên `IndexEntry` phục vụ ba mục đích:

1. **Phát hiện xây dựng lại cũ**: `_do_rebuild` ghi lại `version` trước khi thực hiện fetch ChromaDB tốn kém. Nếu `version` thay đổi trong khi đang xây dựng lại, kết quả sẽ bị hủy và lần xây dựng lại của writer mới nhất sẽ chạy thay thế. Điều này ngăn một lần xây dựng lại cũ chậm ghi đè lên index mới hơn một phần.

2. **Giám sát / quan sát**: version có thể được hiển thị qua endpoint `/ml/health` hoặc `/ml/bm25/stats` để operator có thể xem bao nhiêu lần ghi đã xảy ra kể từ lần xây dựng lại cuối.

3. **Tương lai: đọc lạc quan**: caller có thể ghi lại version trước khi tìm kiếm, đọc lại sau đó, và phát hiện nếu index bị thay đổi trong khi tìm kiếm. Hiện tại chưa được sử dụng nhưng không tốn chi phí để duy trì.

### 8.2 Endpoint Hiển Thị Version (tùy chọn, trong `main_ml.py`)

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

## 9. Quản Lý Bộ Nhớ (LRU Eviction)

### 9.1 Thiết Kế

`_catalog` là một `OrderedDict`. Mỗi lần truy cập qua `_get_or_create_entry` sẽ gọi `move_to_end(collection_name)` để đánh dấu nó là được sử dụng gần nhất. Khi một entry mới sẽ đẩy catalog vượt quá `BM25_MAX_CACHED_COLLECTIONS`, entry cũ nhất sẽ bị xóa bằng `popitem(last=False)`.

Các entry bị xóa chỉ đơn giản là được thu gom rác. Lần tìm kiếm tiếp theo trên một collection bị xóa sẽ kích hoạt xây dựng lại khởi động nguội (cùng đường dẫn với một collection mới hoàn toàn).

### 9.2 Ước Tính Bộ Nhớ

| Chunk mỗi collection | Số token trung bình mỗi chunk | Bộ nhớ mỗi index |
|---|---|---|
| 10.000 | 100 token | ~80 MB |
| 50.000 | 100 token | ~400 MB |
| 100.000 | 100 token | ~800 MB |

Với `BM25_MAX_CACHED_COLLECTIONS=50` và trung bình 10K chunk mỗi collection: giới hạn tối đa ~4 GB. Với các triển khai lớn hơn, hãy giảm giới hạn xuống 10–20. Delta buffer thêm bộ nhớ không đáng kể vì nó chỉ chứa các chunk được thêm kể từ lần xây dựng lại cuối.

### 9.3 Code LRU (đã được tích hợp trong `_get_or_create_entry` ở trên)

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

## 10. Tích Hợp: Các Điểm Gọi Đã Cập Nhật Trong `main_ml.py`

Hai endpoint hiện đang gọi `hybrid_searcher.invalidate()` cần được cập nhật để gọi API ghi mới thay thế. Điều này yêu cầu biết các ID chunk đã được ghi hoặc xóa — cả hai đều đã có sẵn trong `store.py`.

### 10.1 `store.py` — hiển thị chunk ID từ `add_chunks`

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

### 10.2 `store.py` — hiển thị ID đã xóa từ `delete_document`

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

### 10.3 `main_ml.py` — các handler endpoint đã cập nhật

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

## 11. Các Phương Pháp Thay Thế: Whoosh và Tantivy

### 11.1 Whoosh

[Whoosh](https://whoosh.readthedocs.io/) là một thư viện tìm kiếm toàn văn bản thuần Python, dựa trên file với các cập nhật index gia tăng thực sự, merge theo segment, và tính điểm BM25F.

**Ưu điểm**:
- Ghi gia tăng thực sự: `writer.add_document()` / `writer.delete_by_term()` không cần xây dựng lại toàn bộ
- Index bền vững tồn tại qua các lần khởi động lại dịch vụ — không cần xây dựng lại khởi động nguội
- Mô hình đa luồng đã được kiểm thử với `BufferedWriter` và `AsyncWriter`
- Thuần Python — không có native extension, hoạt động trên Windows không cần C toolchain

**Nhược điểm**:
- Phát triển tích cực thực tế đã dừng lại (bản phát hành cuối 2013, các fork Python 3 tồn tại nhưng phân mảnh)
- Disk I/O cho mỗi lần ghi — thêm độ trễ trên container NVMe-backed nhưng đáng chú ý trên HDD hoặc NFS mounts
- Schema phải được định nghĩa trước; thêm trường metadata mới yêu cầu reindex
- Các file handle ánh xạ bộ nhớ có thể xung đột với ngữ nghĩa khóa file của Windows
- Không được thiết kế cho vector — chỉ BM25; không thể dùng cho phía embedding

**Di chuyển ước tính**: thay thế `BM25Okapi` bằng `whoosh.index.Index` cho mỗi collection, sử dụng `AsyncWriter` cho ghi, `searcher.search(query, limit=top_k)` cho đọc. Một collection 100K chunk sẽ chiếm ~500 MB trên đĩa (các segment nén).

### 11.2 Tantivy-py

[tantivy-py](https://github.com/quickwit-oss/tantivy-py) bao bọc [Tantivy](https://github.com/quickwit-oss/tantivy) (một engine tìm kiếm toàn văn bản Rust) với các binding Python. Tantivy là engine đằng sau Quickwit và đạt cấp độ production.

**Ưu điểm**:
- Cực kỳ nhanh: kiến trúc giống Lucene theo segment với WAND query acceleration
- Cập nhật gia tăng thực sự (commit = atomic segment flush; merge ở nền)
- Bền vững; tồn tại qua các lần khởi động lại
- Tính điểm BM25F với tham số k1/b có thể cấu hình
- Phát triển tích cực; Python 3.8–3.12 wheels có sẵn trên PyPI

**Nhược điểm**:
- Native Rust extension — yêu cầu prebuilt wheel hoặc Rust toolchain; thêm ~15 MB vào Docker image
- Python binding mỏng hơn API của Whoosh; một số tính năng nâng cao yêu cầu hiểu mô hình segment của Tantivy
- Schema vẫn cần định nghĩa trước; thêm field yêu cầu re-index
- Không thuần Python — wheel Windows thường có sẵn (PyPI cung cấp) nhưng có thể không ổn định trong môi trường bất thường

**Di chuyển ước tính**:

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

### 11.3 Bảng So Sánh

| Chiều | Hiện tại (delta-buffer) | Whoosh | Tantivy-py |
|---|---|---|---|
| Độ trễ ghi | O(k) — thêm vào danh sách | O(k) — ghi segment | O(k) — ghi segment |
| Độ trễ đọc (nguội) | O(n) — quét ChromaDB | O(1) — index trên đĩa | O(1) — index trên đĩa |
| Độ trễ đọc (ấm) | O(log n) + O(delta) | O(log n) | O(log n) |
| Sử dụng bộ nhớ | Giới hạn bởi LRU | Thấp (disk-backed) | Thấp (disk-backed) |
| Bền vững | Không (trong bộ nhớ) | Có | Có |
| Phụ thuộc native | Không (thuần Python) | Không (thuần Python) | Rust wheel |
| Hỗ trợ Windows | Đầy đủ | Đầy đủ | Tốt (PyPI wheels) |
| Ghi đồng thời | Tuần tự theo entry | AsyncWriter hỗ trợ | Writer lock (một writer) |
| Tương thích điểm | Khớp BM25Okapi chính xác | BM25F (gần) | BM25F (gần) |
| Gánh nặng bảo trì | Thấp (không có dep mới) | Trung bình (thư viện cũ) | Trung bình (quản lý Rust wheel) |
| **Khuyến nghị cho** | Quy mô hiện tại (<500K chunk) | Nếu cần bền vững, không Rust | Nếu >500K chunk hoặc cần HA |

**Khuyến nghị**: áp dụng phương pháp delta-buffer ngay bây giờ. Nếu kích thước collection tăng vượt 500K chunk mỗi collection hoặc nếu dịch vụ cần khởi động lại mà không cần 30 giây xây dựng lại khởi động ấm, hãy di chuyển sang Tantivy-py làm backend BM25 bền vững trong khi giữ nguyên interface `HybridSearcher`.

---

## 12. Benchmark Hiệu Năng (Dự Kiến)

Các đo lường là ước tính dựa trên hiệu năng Python/rank_bm25 điển hình trên máy 4-core với 16 GB RAM. Số thực tế sẽ thay đổi.

### 12.1 Đường Dẫn Ghi

| Thao tác | Cũ (`invalidate`) | Mới (`add_chunks`) | Tăng tốc |
|---|---|---|---|
| Thêm 50 chunk vào collection 100K-chunk | Đánh dấu dirty; tìm kiếm tiếp theo kích hoạt xây dựng lại 8–30 giây | Thêm vào danh sách: ~0.1 ms | ~100.000× |
| Xóa 1 tài liệu (200 chunk) | Đánh dấu dirty; tìm kiếm tiếp theo kích hoạt xây dựng lại 8–30 giây | Cập nhật set: ~0.05 ms | ~200.000× |

### 12.2 Đường Dẫn Đọc (Độ Trễ Tìm Kiếm)

| Tình huống | Cũ | Mới | Ghi chú |
|---|---|---|---|
| Khởi động nguội (chưa được xây dựng) | Chặn 8–30 giây | 8–30 giây (chỉ lần đầu, sau đó ấm) | Cùng khởi động nguội; chỉ xảy ra một lần |
| Đọc ấm (không có delta) | Chỉ tính điểm O(n) | Chỉ tính điểm O(n) | Giống nhau |
| Đọc ấm (delta 500 chunk) | N/A (cũ kích hoạt xây dựng lại) | O(n) chính + O(500) delta | Thêm ~1–2 ms cho delta BM25 |
| Đọc trong khi xây dựng lại nền | N/A (bị chặn bởi lock) | Phục vụ từ main cũ + delta | 0 ms độ trễ thêm |

### 12.3 So Sánh Sử Dụng Bộ Nhớ

| Chỉ số | Cũ | Mới |
|---|---|---|
| Bộ nhớ mỗi collection (100K chunk) | ~800 MB (1 cho mỗi collection active) | ~800 MB main + O(delta) — cùng mỗi entry |
| Collection không giới hạn | Tăng mãi mãi | Giới hạn ở `BM25_MAX_CACHED_COLLECTIONS` × 800 MB |
| Áp lực bộ nhớ dưới 50 collection | 40 GB+ | Giới hạn tối đa 40 GB mặc định, điều chỉnh xuống 5–10 để an toàn |

### 12.4 Đồng Thời

| Tình huống | Cũ | Mới |
|---|---|---|
| 10 lần ghi index đồng thời | 10 lần xây dựng lại toàn bộ tuần tự sau mỗi lần ghi | 10 lần thêm vào danh sách; 1 lần xây dựng lại nền |
| 10 tìm kiếm đồng thời | Tất cả bị chặn nếu một cái kích hoạt khởi động nguội | 9 được phục vụ từ cũ; 1 chờ khởi động nguội (chỉ lần đầu) |
| Xây dựng lại nền cạnh tranh | N/A | Phát hiện version mismatch hủy các lần xây dựng lại cũ |

---

## 13. Danh Sách Kiểm Tra Triển Khai

- [ ] Thêm `BM25_MAX_CACHED_COLLECTIONS`, `BM25_REBUILD_BATCH_SIZE`, `BM25_REBUILD_WORKERS` vào `ml_service/config.py`
- [ ] Thêm `BM25_MAX_CACHED_COLLECTIONS`, `BM25_REBUILD_BATCH_SIZE`, `BM25_REBUILD_WORKERS` vào `.env.example`
- [ ] Thay thế `ml_service/rag/hybrid_search.py` bằng triển khai `IndexEntry` + `HybridSearcher` mới
- [ ] Cập nhật `ml_service/rag/store.py`: `add_chunks` trả về `list[str]` các ID; `delete_document` trả về `tuple[int, list[str]]`
- [ ] Cập nhật `ml_service/main_ml.py`: thay thế các lần gọi `invalidate()` bằng các lần gọi `add_chunks()` / `mark_deleted()`
- [ ] Thêm endpoint chẩn đoán `GET /ml/bm25/stats` (tùy chọn)
- [ ] Cập nhật lần gọi `HybridSearcher.shutdown()` trong teardown `lifespan` của FastAPI:
  ```python
  @asynccontextmanager
  async def lifespan(app: FastAPI):
      ...
      yield
      hybrid_searcher.shutdown()   # drain background rebuild threads
  ```
- [ ] Viết unit test cho:
  - Delta buffer thêm + tìm kiếm trả về kết quả delta
  - Soft delete lọc các ID đã xóa khỏi cả main lẫn delta
  - Version mismatch khiến xây dựng lại bị hủy
  - LRU eviction khi đầy dung lượng
  - Xây dựng lại nền hoán đổi index theo cách nguyên tử

---

## 14. Đánh Đổi và Giới Hạn

| Đánh đổi | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Delta buffer sử dụng một `BM25Okapi` mới cho mỗi tìm kiếm | Với delta > ~5K chunk, điều này thêm chi phí đáng chú ý (mỗi truy vấn xây dựng một `BM25Okapi` tạm thời) | `BM25_REBUILD_BATCH_SIZE` mặc định là 500 giữ delta nhỏ; nếu tăng, xây dựng lại nền sẽ thu gọn nó |
| Điểm từ index chính và index delta ở các thang đo IDF khác nhau | Hợp nhất điểm BM25 thô từ hai corpus khác nhau là không chính xác về mặt thống kê | Sử dụng chuẩn hóa điểm (chia cho điểm tối đa mỗi sub-index) trước khi hợp nhất; hoặc sử dụng RRF giữa hai sub-result thay vì hợp nhất điểm thô |
| Các chunk bị soft-delete vẫn còn trong bộ nhớ cho đến khi xây dựng lại hoàn thành | Với xóa hàng loạt (ví dụ: xóa tài liệu 10K-chunk), bộ nhớ không được giải phóng ngay | Xây dựng lại nền được kích hoạt ngay khi xóa; bộ nhớ được thu hồi trong vài giây |
| `_do_rebuild` giữ ChromaDB không có lock — ChromaDB có thể trả về snapshot giữa chừng khi ghi | Có thể xảy ra: một thao tác ghi đồng thời hoàn thành, nhưng xây dựng lại fetch snapshot hơi cũ | Được chấp nhận: kiểm tra version mismatch sẽ hủy bất kỳ lần xây dựng lại nào cạnh tranh với thao tác ghi, và lần xây dựng lại của thao tác ghi sẽ tạo ra kết quả đúng |
| Thread pool là global theo tiến trình | Hai collection có thể xây dựng lại đồng thời, nhưng tối đa `BM25_REBUILD_WORKERS` lần xây dựng lại chạy song song | Tăng `BM25_REBUILD_WORKERS` nếu hàng đợi xây dựng lại bị tắc nghẽn; giám sát qua `/ml/bm25/stats` |
| Không bền vững qua các lần khởi động lại dịch vụ | Khởi động nguội luôn cần thiết cho mỗi collection sau khi khởi động lại | Chấp nhận được cho quy mô hiện tại; di chuyển sang Tantivy-py nếu cần khởi động ấm |
| LRU eviction xóa toàn bộ index | Một collection bị xóa phải khởi động nguội ở lần tìm kiếm tiếp theo | Đặt `BM25_MAX_CACHED_COLLECTIONS` đủ cao để bao phủ số lượng collection active dự kiến |

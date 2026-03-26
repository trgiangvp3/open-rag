# BM25 Persistent Index với Tantivy — Tài Liệu Thiết Kế

**Ngày**: 2026-03-26
**Tác giả**: Architecture Review
**Trạng thái**: Đề xuất
**Thay thế**: `design-bm25-incremental.md` (phương pháp delta-buffer)
**Files mục tiêu**: `ml_service/rag/hybrid_search.py`, `ml_service/rag/store.py`, `ml_service/main_ml.py`, `ml_service/config.py`

---

## 1. Mô Tả Vấn Đề

*(Giống design doc trước — tóm tắt lại)*

`HybridSearcher` hiện tại dùng `rank_bm25.BM25Okapi` — một thư viện không có API gia tăng. Mỗi thao tác ghi (add/delete) buộc phải rebuild toàn bộ index từ ChromaDB (5–30 giây cho 100K chunks). Index chỉ tồn tại trong bộ nhớ — mỗi lần restart service mất cold start 8–30 giây/collection.

### Tại Sao Không Dùng Delta-Buffer?

Design doc `design-bm25-incremental.md` đề xuất delta buffer + background rebuild + soft deletes. Phương pháp đó giải quyết write path nhưng:

| Vấn đề | Delta-buffer | Tantivy |
|---|---|---|
| Persistence (tồn tại qua restart) | **Không** — cold start 8–30s | **Có** — index trên disk, khởi động ~0ms |
| Incremental add | Append vào buffer, rebuild nền | **True incremental** — segment flush |
| Incremental delete | Soft delete + rebuild nền | **True delete** — tombstone file |
| IDF accuracy | Main + delta có IDF khác nhau | **Chính xác** — IDF thống nhất per segment |
| Code complexity | ~200 LOC mới (version counter, soft deletes, thread pool, LRU) | ~80 LOC adapter |
| External dependency | Không (giữ rank_bm25) | Thêm `tantivy` Rust wheel (~15 MB) |

**Kết luận**: Tantivy giải quyết triệt để cả persistence lẫn incremental với ít code hơn. Trade-off duy nhất là thêm Rust wheel.

---

## 2. Tổng Quan Giải Pháp

### Kiến Trúc Mới

```
┌─────────────────────────────────────────────────────────┐
│  HybridSearcher (hybrid_search.py)                      │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  TantivyBM25Index                               │    │
│  │  - Per-collection tantivy.Index trên disk       │    │
│  │  - Schema: chunk_id (stored), body (indexed),   │    │
│  │            metadata (stored JSON)               │    │
│  │  - Writer: add_document / delete_documents      │    │
│  │  - Searcher: search(query, limit=top_k)         │    │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  hybrid_search() ← RRF fusion (giữ nguyên)             │
└─────────────────────────────────────────────────────────┘
         │                              │
    add_chunks()                  bm25_search()
    mark_deleted()
```

### Nguyên Tắc Thiết Kế

1. **Giữ nguyên interface `HybridSearcher`** — `main_ml.py` chỉ cần thay đổi tối thiểu
2. **Mỗi collection = 1 tantivy Index trên disk** tại `data/bm25/<collection_name>/`
3. **Writer là thread-safe** — tantivy chỉ cho phép 1 writer/index, nhưng writer có thể được gọi từ nhiều thread (nó tự serialize)
4. **Searcher được tạo lại sau mỗi commit** để thấy dữ liệu mới
5. **Xóa `rank_bm25` dependency** hoàn toàn

---

## 3. Cấu Hình

### 3.1 Biến Môi Trường Mới

Thêm vào `ml_service/config.py`:

```python
# ── BM25 (Tantivy) configuration ─────────────────────────────────────────
BM25_INDEX_DIR = DATA_DIR / "bm25"
BM25_INDEX_DIR.mkdir(parents=True, exist_ok=True)

# Heap size for tantivy writer (bytes). Higher = faster bulk indexing.
# 50 MB default is suitable for most workloads.
BM25_WRITER_HEAP_SIZE: int = int(os.getenv("BM25_WRITER_HEAP_SIZE", str(50_000_000)))
```

### 3.2 Cập Nhật `.env.example`

```
# BM25 Tantivy index
# BM25_WRITER_HEAP_SIZE=50000000
```

Không cần `BM25_MAX_CACHED_COLLECTIONS`, `BM25_REBUILD_BATCH_SIZE`, `BM25_REBUILD_WORKERS` nữa — Tantivy quản lý bộ nhớ và segment merge tự động.

---

## 4. Schema Tantivy

Mỗi collection có một schema cố định với 3 fields:

```python
schema_builder = tantivy.SchemaBuilder()

# Stored + indexed as unique key — dùng cho delete-by-term
schema_builder.add_text_field("chunk_id", stored=True, tokenizer_name="raw")

# Full-text indexed + stored — đây là nội dung để BM25 scoring
schema_builder.add_text_field("body", stored=True)

# Stored only (không indexed) — metadata dạng JSON string
schema_builder.add_text_field("metadata", stored=True, tokenizer_name="raw")

schema = schema_builder.build()
```

**Tại sao `metadata` là JSON string thay vì `json_field`?**
- Metadata trong OpenRAG là dict tùy ý (document_id, indexed_at, user fields...)
- `json_field` của tantivy yêu cầu schema cố định
- Lưu dạng JSON string trong `stored=True` field đơn giản hơn và flexible hơn
- Khi đọc: `json.loads(doc["metadata"])` để khôi phục dict

---

## 5. Triển Khai Chi Tiết

### 5.1 `ml_service/rag/hybrid_search.py` — Thay thế hoàn toàn

```python
"""Hybrid search: BM25 (Tantivy) + semantic via Reciprocal Rank Fusion.

BM25 indexes are maintained per-collection using Tantivy — a Rust-based
full-text search engine with:
- True incremental add/delete (segment-based, no full rebuild)
- Persistent on-disk indexes (survives service restarts, zero cold start)
- BM25 scoring built-in
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import tantivy

from config import BM25_INDEX_DIR, BM25_WRITER_HEAP_SIZE

logger = logging.getLogger(__name__)


class TantivyBM25Index:
    """Manages a single Tantivy index for one collection."""

    def __init__(self, collection_name: str, index_dir: Path):
        self._collection_name = collection_name
        self._path = index_dir / collection_name
        self._path.mkdir(parents=True, exist_ok=True)

        # Build schema
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("chunk_id", stored=True, tokenizer_name="raw")
        builder.add_text_field("body", stored=True)
        builder.add_text_field("metadata", stored=True, tokenizer_name="raw")
        self._schema = builder.build()

        # Open or create index
        try:
            self._index = tantivy.Index(self._schema, path=str(self._path))
        except Exception:
            # If index exists but schema mismatch, rebuild from scratch
            logger.warning("Rebuilding Tantivy index for '%s' (schema mismatch)", collection_name)
            import shutil
            shutil.rmtree(self._path, ignore_errors=True)
            self._path.mkdir(parents=True, exist_ok=True)
            self._index = tantivy.Index(self._schema, path=str(self._path))

        self._writer = self._index.writer(heap_size=BM25_WRITER_HEAP_SIZE)
        self._lock = threading.Lock()  # guards writer operations

        logger.info("Tantivy BM25 index opened for '%s' at %s", collection_name, self._path)

    def add_documents(
        self,
        chunk_ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """Add chunks to the index. Incremental — no full rebuild needed."""
        with self._lock:
            for cid, text, meta in zip(chunk_ids, texts, metadatas):
                self._writer.add_document(tantivy.Document(
                    chunk_id=[cid],
                    body=[text],
                    metadata=[json.dumps(meta, ensure_ascii=False)],
                ))
            self._writer.commit()
        self._index.reload()
        logger.debug("Added %d chunks to Tantivy index '%s'", len(chunk_ids), self._collection_name)

    def delete_documents(self, chunk_ids: list[str]) -> None:
        """Delete chunks by ID. Uses tantivy tombstone mechanism."""
        if not chunk_ids:
            return
        with self._lock:
            for cid in chunk_ids:
                self._writer.delete_documents("chunk_id", cid)
            self._writer.commit()
        self._index.reload()
        logger.debug("Deleted %d chunks from Tantivy index '%s'", len(chunk_ids), self._collection_name)

    def search(self, query: str, top_k: int) -> list[dict]:
        """BM25 search. Returns list of {text, metadata, score, id}."""
        searcher = self._index.searcher()
        parsed_query = self._index.parse_query(query, ["body"])

        try:
            hits = searcher.search(parsed_query, limit=top_k).hits
        except Exception:
            logger.exception("Tantivy search failed for '%s'", self._collection_name)
            return []

        results = []
        for score, doc_address in hits:
            doc = searcher.doc(doc_address)
            # tantivy returns field values as lists
            text = doc["body"][0] if doc.get("body") else ""
            chunk_id = doc["chunk_id"][0] if doc.get("chunk_id") else ""
            meta_str = doc["metadata"][0] if doc.get("metadata") else "{}"
            try:
                metadata = json.loads(meta_str)
            except json.JSONDecodeError:
                metadata = {}

            results.append({
                "text": text,
                "metadata": metadata,
                "score": round(float(score), 4),
                "id": chunk_id,
            })
        return results

    def delete_all(self) -> None:
        """Delete all documents (used when collection is deleted)."""
        with self._lock:
            self._writer.delete_all_documents()
            self._writer.commit()
        self._index.reload()

    def doc_count(self) -> int:
        """Return approximate number of documents in the index."""
        searcher = self._index.searcher()
        return searcher.num_docs


class HybridSearcher:
    """Manages per-collection BM25 indexes backed by Tantivy.

    Thread-safety: each TantivyBM25Index has its own lock for writer ops.
    _indexes dict is guarded by _catalog_lock.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, TantivyBM25Index] = {}
        self._catalog_lock = threading.Lock()

    def _get_or_create_index(self, collection_name: str) -> TantivyBM25Index:
        """Get or lazily create a Tantivy index for the collection."""
        with self._catalog_lock:
            if collection_name not in self._indexes:
                self._indexes[collection_name] = TantivyBM25Index(
                    collection_name, BM25_INDEX_DIR
                )
            return self._indexes[collection_name]

    # ── Write API ────────────────────────────────────────────────────────────

    def add_chunks(
        self,
        collection_name: str,
        chunk_ids: list[str],
        texts: list[str],
        metadatas: list[dict],
    ) -> None:
        """Add chunks to the BM25 index. Incremental — no rebuild."""
        idx = self._get_or_create_index(collection_name)
        idx.add_documents(chunk_ids, texts, metadatas)

    def mark_deleted(self, collection_name: str, chunk_ids: list[str]) -> None:
        """Delete chunks from the BM25 index by ID."""
        idx = self._get_or_create_index(collection_name)
        idx.delete_documents(chunk_ids)

    def invalidate(self, collection_name: str) -> None:
        """Backward-compatible: no-op since Tantivy index is always up to date.

        Kept so callers that haven't migrated to add_chunks/mark_deleted still work.
        """
        pass

    def delete_collection(self, collection_name: str) -> None:
        """Delete the entire Tantivy index for a collection."""
        with self._catalog_lock:
            idx = self._indexes.pop(collection_name, None)
        if idx:
            idx.delete_all()
        # Also remove disk files
        import shutil
        index_path = BM25_INDEX_DIR / collection_name
        shutil.rmtree(index_path, ignore_errors=True)
        logger.info("Deleted Tantivy index for '%s'", collection_name)

    # ── Search API ───────────────────────────────────────────────────────────

    def bm25_search(self, collection_name: str, query: str, top_k: int) -> list[dict]:
        """Return top-k chunks ranked by BM25 score."""
        idx = self._get_or_create_index(collection_name)
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

### 5.2 `HybridSearcher` Không Còn Phụ Thuộc `VectorStore`

Sự thay đổi quan trọng: `HybridSearcher.__init__()` **không còn nhận `VectorStore`** nữa.

Lý do: với `rank_bm25`, `HybridSearcher` cần `VectorStore` để gọi `collection.get()` và fetch toàn bộ chunks từ ChromaDB mỗi lần rebuild. Với Tantivy, index tự quản lý dữ liệu của mình — chunks được thêm trực tiếp qua `add_chunks()` và persist trên disk.

Điều này giảm coupling giữa hai module.

---

## 6. Tích Hợp: Các Thay Đổi Trong Các File Khác

### 6.1 `ml_service/config.py` — Thêm config BM25

```python
# ── BM25 (Tantivy) configuration ─────────────────────────────────────────
BM25_INDEX_DIR = DATA_DIR / "bm25"
BM25_INDEX_DIR.mkdir(parents=True, exist_ok=True)

BM25_WRITER_HEAP_SIZE: int = int(os.getenv("BM25_WRITER_HEAP_SIZE", str(50_000_000)))
```

### 6.2 `ml_service/rag/store.py` — Hiển thị chunk IDs

Hai method cần thay đổi:

**`add_chunks`**: trả về `list[str]` các chunk IDs (thay vì `None`)

```python
    def add_chunks(
        self,
        collection_name: str,
        document_id: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> list[str]:                          # <-- return type changed
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
        return ids                           # <-- now returns IDs
```

**`delete_document`**: trả về `tuple[int, list[str]]` (count + IDs)

```python
    def delete_document(self, collection_name: str, document_id: str) -> tuple[int, list[str]]:
        collection = self.get_or_create_collection(collection_name)
        results = collection.get(where={"document_id": document_id}, include=[])
        if results["ids"]:
            collection.delete(ids=results["ids"])
            logger.info(f"Deleted {len(results['ids'])} chunks for doc {document_id}")
            return len(results["ids"]), results["ids"]
        return 0, []
```

### 6.3 `ml_service/main_ml.py` — Cập nhật endpoints

**Thay đổi 1**: `HybridSearcher()` không nhận `store` nữa

```python
# Trước:
hybrid_searcher = HybridSearcher(store)

# Sau:
hybrid_searcher = HybridSearcher()
```

**Thay đổi 2**: `/ml/index` — dùng `add_chunks` thay vì `invalidate`

```python
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
    chunk_ids = store.add_chunks(               # now returns IDs
        collection_name=req.collection,
        document_id=req.document_id,
        texts=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    hybrid_searcher.add_chunks(                 # incremental add
        collection_name=req.collection,
        chunk_ids=chunk_ids,
        texts=texts,
        metadatas=metadatas,
    )

    return IndexResponse(document_id=req.document_id, chunk_count=len(texts))
```

**Thay đổi 3**: `/ml/documents/delete` — dùng `mark_deleted` thay vì `invalidate`

```python
@app.post("/ml/documents/delete", response_model=DeleteDocumentResponse)
async def delete_document(req: DeleteDocumentRequest):
    """Delete all chunks of a document from ChromaDB."""
    deleted_count, deleted_ids = store.delete_document(req.collection, req.document_id)
    hybrid_searcher.mark_deleted(               # incremental delete
        collection_name=req.collection,
        chunk_ids=deleted_ids,
    )
    return DeleteDocumentResponse(chunks_deleted=deleted_count)
```

**Thay đổi 4**: `/ml/collections/delete` — xóa cả Tantivy index

```python
@app.post("/ml/collections/delete", response_model=OkResponse)
async def delete_collection(req: CollectionRequest):
    """Delete a ChromaDB collection and its BM25 index."""
    try:
        store.delete_collection(req.name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    hybrid_searcher.delete_collection(req.name)  # clean up Tantivy index
    return OkResponse()
```

### 6.4 `ml_service/requirements.txt` — Thay dependency

```
# Xóa:
rank-bm25>=0.2

# Thêm:
tantivy>=0.22
```

### 6.5 `.env.example` — Thêm config

```
# BM25 Tantivy index
# BM25_WRITER_HEAP_SIZE=50000000
```

---

## 7. Data Migration: Dữ Liệu Cũ

### Vấn Đề

Khi chuyển từ `rank_bm25` (in-memory) sang Tantivy (on-disk), index Tantivy ban đầu sẽ **trống** — không có dữ liệu BM25 cho các chunks đã tồn tại trong ChromaDB.

### Giải Pháp: One-Time Migration Script

Thêm endpoint `/ml/bm25/rebuild` và management script:

```python
# ml_service/main_ml.py — thêm endpoint migration

@app.post("/ml/bm25/rebuild", response_model=OkResponse)
async def rebuild_bm25(req: CollectionRequest):
    """One-time: rebuild Tantivy BM25 index from ChromaDB data.

    Use this after migrating from rank_bm25 to backfill existing chunks.
    """
    collection = store.get_or_create_collection(req.name)
    result = collection.get(include=["documents", "metadatas"])
    ids: list[str] = result.get("ids") or []
    texts: list[str] = result.get("documents") or []
    metadatas: list[dict] = result.get("metadatas") or []

    if not ids:
        return OkResponse()

    # Clear existing index and rebuild
    hybrid_searcher.delete_collection(req.name)
    hybrid_searcher.add_chunks(
        collection_name=req.name,
        chunk_ids=ids,
        texts=texts,
        metadatas=metadatas,
    )
    logger.info("Rebuilt Tantivy BM25 index for '%s': %d chunks", req.name, len(ids))
    return OkResponse()
```

### Quy Trình Migration

1. Deploy bản mới với Tantivy
2. Gọi `POST /ml/bm25/rebuild` cho mỗi collection đã tồn tại
3. Từ đó về sau, mọi add/delete đều tự động incremental
4. Sau khi xác nhận mọi thứ hoạt động, có thể xóa endpoint `/ml/bm25/rebuild` hoặc giữ lại cho disaster recovery

---

## 8. Endpoint Chẩn Đoán (Tùy Chọn)

```python
# ml_service/main_ml.py

class BM25StatsEntry(BaseModel):
    doc_count: int
    index_path: str

class BM25StatsResponse(BaseModel):
    collections: dict[str, BM25StatsEntry]

@app.get("/ml/bm25/stats", response_model=BM25StatsResponse)
async def bm25_stats():
    """Return BM25 Tantivy index stats for all loaded collections."""
    stats: dict[str, BM25StatsEntry] = {}
    with hybrid_searcher._catalog_lock:
        for name, idx in hybrid_searcher._indexes.items():
            stats[name] = BM25StatsEntry(
                doc_count=idx.doc_count(),
                index_path=str(idx._path),
            )
    return BM25StatsResponse(collections=stats)
```

---

## 9. Tokenization: Vietnamese Support

### Vấn Đề

Tantivy sử dụng tokenizer mặc định là `SimpleTokenizer` (tách bằng whitespace + lowercase). Điều này hoạt động tốt cho tiếng Việt vì tiếng Việt có dấu cách giữa các âm tiết (syllables).

Ví dụ: `"Hệ thống truy xuất thông tin"` → `["hệ", "thống", "truy", "xuất", "thông", "tin"]`

### So Sánh Với `rank_bm25` Hiện Tại

Code hiện tại dùng `text.lower().split()` — cũng là whitespace tokenization. Vậy **kết quả scoring sẽ tương đương**.

### Tương Lai

Nếu cần tokenization tốt hơn cho tiếng Việt (compound words: "truy xuất" thay vì "truy" + "xuất"), có thể:
- Đăng ký custom tokenizer với Tantivy (ICU tokenizer)
- Hoặc pre-tokenize trước khi index (giống cách hiện tại)

Không cần thiết cho v1 — whitespace tokenization đủ tốt.

---

## 10. Benchmark Hiệu Năng (Dự Kiến)

### 10.1 Đường Dẫn Ghi

| Thao tác | rank_bm25 (hiện tại) | Delta-buffer | Tantivy |
|---|---|---|---|
| Thêm 50 chunks vào 100K-chunk collection | Full rebuild 8–30s | O(1) append + rebuild nền | **~5ms** (segment flush) |
| Xóa 1 tài liệu (200 chunks) | Full rebuild 8–30s | Soft delete + rebuild nền | **~2ms** (tombstone) |

### 10.2 Đường Dẫn Đọc

| Tình huống | rank_bm25 | Delta-buffer | Tantivy |
|---|---|---|---|
| Cold start (sau restart) | 8–30s rebuild từ ChromaDB | 8–30s rebuild từ ChromaDB | **~0ms** (đọc từ disk) |
| Warm search | O(n) linear scan | O(n) + O(delta) | **O(log n)** WAND acceleration |
| Search trong khi ghi | Bị block | Phục vụ từ cũ + delta | **Không bị block** (MVCC) |

### 10.3 Bộ Nhớ

| Chỉ số | rank_bm25 | Delta-buffer | Tantivy |
|---|---|---|---|
| 100K chunks in memory | ~800 MB | ~800 MB + delta | **~50 MB** (disk-backed, mmap) |
| 50 collections | 40 GB+ (không giới hạn) | 40 GB (LRU) | **~2.5 GB on disk**, ~500 MB RAM (mmap) |

### 10.4 Disk Usage

| Collection size | Index on disk |
|---|---|
| 10K chunks | ~50 MB |
| 50K chunks | ~250 MB |
| 100K chunks | ~500 MB |

---

## 11. Bảng So Sánh Tổng Hợp: 3 Phương Pháp

| Chiều | rank_bm25 (hiện tại) | Delta-buffer (design cũ) | Tantivy (design này) |
|---|---|---|---|
| Write latency | O(n) rebuild | O(1) amortised | O(1) segment flush |
| Read latency (cold) | 8–30s | 8–30s | **~0ms** |
| Read latency (warm) | O(n) | O(n) + O(delta) | **O(log n)** |
| Persistence | Không | Không | **Có** |
| IDF accuracy | Chính xác | Xấp xỉ (main ≠ delta) | **Chính xác** |
| Memory usage | Không giới hạn | LRU bounded | **Disk-backed** |
| Code complexity | 103 LOC | ~300 LOC | **~180 LOC** |
| New dependencies | rank_bm25 | rank_bm25 | tantivy (Rust wheel) |
| Concurrent reads | Bị block khi rebuild | Không block | **MVCC** |
| Concurrent writes | Serialize dưới 1 lock | Per-entry lock + version | **Writer lock** (1 writer/index) |
| Docker image size | Baseline | Baseline | **+~15 MB** |
| Khởi động service | Lazy build từ ChromaDB | Lazy build từ ChromaDB | **Instant** (đọc disk) |

---

## 12. Rủi Ro và Giảm Thiểu

| Rủi ro | Ảnh hưởng | Giảm thiểu |
|---|---|---|
| Tantivy-py wheel không có cho platform cụ thể | Không thể cài đặt | PyPI cung cấp wheels cho Linux x86_64, macOS, Windows. Các platform khác cần Rust toolchain |
| Tantivy API thay đổi giữa các version | Code bị break | Pin `tantivy>=0.22,<1.0` trong requirements |
| Disk space cho index | Tốn dung lượng | ~500 MB / 100K chunks — chấp nhận được. Segment merge tự động compact |
| Schema migration nếu thêm field | Cần rebuild index | Hiếm khi xảy ra. Endpoint `/ml/bm25/rebuild` có sẵn |
| Writer lock cho concurrent writes | Serialize writes | Tantivy writer đã thread-safe (tự serialize). Throughput vẫn cao vì mỗi add chỉ ~0.1ms |
| Tantivy default tokenizer khác rank_bm25 | Score khác nhau | Cả hai đều dùng whitespace + lowercase. Kết quả tương đương |

---

## 13. Chiến Lược Kiểm Chứng

**File test**: `ml_service/tests/test_bm25_tantivy.py`
**Chạy**: `cd ml_service && python -m pytest tests/test_bm25_tantivy.py -v -s`

Kiểm chứng trên 3 chiều: **Đúng**, **Nhanh**, **Bền**.

### 13.1 Đúng (Correctness)

| Test class | Kiểm tra |
|---|---|
| `TestAddAndSearch` | Add chunks → search trả đúng kết quả, đúng metadata, đúng top_k |
| `TestDelete` | Delete → chunk biến mất khỏi kết quả; delete nonexistent/empty không crash |
| `TestHybridSearch` | RRF fusion merge semantic + BM25 đúng; respects top_k |
| `TestDeleteCollection` | Xóa collection → index trống, search trả [] |
| `TestCompareWithRankBM25` | So sánh trực tiếp top-3 giữa `rank_bm25` cũ và `tantivy` mới — ít nhất 1/3 kết quả trùng nhau |

### 13.2 Nhanh (Performance)

| Benchmark | Metric | Kỳ vọng Tantivy | rank_bm25 hiện tại |
|---|---|---|---|
| `test_bench_bulk_add` | Bulk add 10K chunks | < 10s | N/A |
| `test_bench_search_latency` | Avg search latency (10K chunks) | < 100ms | O(n) linear scan |
| `test_bench_incremental_add` | Incremental add 50 chunks vào 10K index | **< 100ms** | **8–30s full rebuild** |
| `test_bench_delete` | Delete 200 chunks từ 10K index | **< 100ms** | **8–30s full rebuild** |
| `test_bench_cold_start` | Open 10K index + first search (sau restart) | **< 500ms** | **8–30s rebuild từ ChromaDB** |

**Tiêu chí thành công chính**: cold start < 500ms và incremental add < 100ms.

### 13.3 Bền (Persistence)

| Test | Kiểm tra |
|---|---|
| `test_data_survives_restart` | Add data → xóa HybridSearcher → tạo mới (cùng disk path) → data vẫn search được |
| `test_delete_persists_across_restart` | Delete chunk → restart → chunk vẫn bị xóa, không xuất hiện lại |

### 13.4 Quy Trình Chạy Test

```bash
# 1. Cài dependencies
cd ml_service
pip install tantivy pytest

# 2. Chạy full test suite với benchmark output
python -m pytest tests/test_bm25_tantivy.py -v -s

# 3. Output mẫu kỳ vọng:
# [BENCH] Bulk add 10K chunks: 2.341s
# [BENCH] Avg search latency (10K chunks): 12.3ms
# [BENCH] Incremental add 50 chunks to 10K index: 45.2ms
# [BENCH] Delete 200 chunks from 10K index: 23.1ms
# [BENCH] Cold start (open 10K index + first search): 89.4ms
# [COMPARE] rank_bm25 top-3: {...}
# [COMPARE] tantivy  top-3: {...}
# [COMPARE] overlap: 2/3
```

### 13.5 Kiểm Chứng Sau Deploy (Manual)

Sau khi deploy lên staging/production:

1. **Migration**: gọi `POST /ml/bm25/rebuild` cho mỗi collection
2. **Smoke test**: gọi `POST /ml/search` với `search_mode: "hybrid"` — verify kết quả có trả về
3. **Stats check**: gọi `GET /ml/bm25/stats` — verify `doc_count` khớp với số chunks trong ChromaDB
4. **Restart test**: restart ML service → gọi search ngay lập tức → verify response < 1s (không có cold start 8–30s)
5. **Write test**: upload document mới → search ngay → verify document mới xuất hiện trong kết quả BM25

---

## 14. Danh Sách Kiểm Tra Triển Khai

- [ ] Thêm `BM25_INDEX_DIR`, `BM25_WRITER_HEAP_SIZE` vào `ml_service/config.py`
- [ ] Thêm `BM25_WRITER_HEAP_SIZE` vào `.env.example`
- [ ] Thay thế `ml_service/rag/hybrid_search.py` bằng triển khai Tantivy (`TantivyBM25Index` + `HybridSearcher`)
- [ ] Cập nhật `ml_service/rag/store.py`: `add_chunks` trả về `list[str]`; `delete_document` trả về `tuple[int, list[str]]`
- [ ] Cập nhật `ml_service/main_ml.py`:
  - `HybridSearcher()` không nhận `store`
  - `/ml/index`: dùng `add_chunks` thay vì `invalidate`
  - `/ml/documents/delete`: dùng `mark_deleted` thay vì `invalidate`
  - `/ml/collections/delete`: gọi thêm `hybrid_searcher.delete_collection()`
- [ ] Thêm endpoint `POST /ml/bm25/rebuild` cho data migration
- [ ] Thêm endpoint `GET /ml/bm25/stats` (tùy chọn)
- [ ] Cập nhật `ml_service/requirements.txt`: thay `rank-bm25>=0.2` bằng `tantivy>=0.22`
- [ ] Chạy test suite: `python -m pytest tests/test_bm25_tantivy.py -v -s`
- [ ] Verify benchmarks: cold start < 500ms, incremental add < 100ms
- [ ] Chạy migration: gọi `/ml/bm25/rebuild` cho mỗi collection hiện có
- [ ] Xóa dependency `rank-bm25` sau khi migration thành công

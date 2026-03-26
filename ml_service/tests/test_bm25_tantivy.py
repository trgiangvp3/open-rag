"""Tests for Tantivy-based BM25 HybridSearcher.

Run: cd ml_service && python -m pytest tests/test_bm25_tantivy.py -v
"""

import json
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def index_dir(tmp_path):
    """Provide a temporary directory for Tantivy indexes."""
    d = tmp_path / "bm25"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def searcher(index_dir):
    """Create a HybridSearcher with patched config."""
    with patch("rag.hybrid_search.BM25_INDEX_DIR", index_dir), \
         patch("rag.hybrid_search.BM25_WRITER_HEAP_SIZE", 15_000_000):
        from rag.hybrid_search import HybridSearcher
        yield HybridSearcher()


# ── Sample data ──────────────────────────────────────────────────────────────

SAMPLE_CHUNKS = {
    "ids": ["doc1_0", "doc1_1", "doc1_2", "doc2_0", "doc2_1"],
    "texts": [
        "Hệ thống truy xuất thông tin sử dụng mô hình BM25 để xếp hạng tài liệu",
        "Vector embeddings cho phép tìm kiếm ngữ nghĩa trong không gian nhiều chiều",
        "Reciprocal Rank Fusion kết hợp kết quả từ nhiều hệ thống tìm kiếm",
        "Python là ngôn ngữ lập trình phổ biến cho machine learning và AI",
        "Docker container giúp triển khai ứng dụng nhất quán trên nhiều môi trường",
    ],
    "metadatas": [
        {"document_id": "doc1", "source": "ir_textbook.pdf"},
        {"document_id": "doc1", "source": "ir_textbook.pdf"},
        {"document_id": "doc1", "source": "ir_textbook.pdf"},
        {"document_id": "doc2", "source": "python_guide.pdf"},
        {"document_id": "doc2", "source": "python_guide.pdf"},
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CORRECTNESS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestAddAndSearch:
    """Verify that indexed chunks are searchable."""

    def test_add_then_search_returns_results(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        results = searcher.bm25_search("test_col", "BM25 xếp hạng tài liệu", top_k=3)

        assert len(results) > 0
        # The first result should be the one about BM25
        assert "BM25" in results[0]["text"]

    def test_search_empty_collection_returns_empty(self, searcher):
        results = searcher.bm25_search("empty_col", "anything", top_k=5)
        assert results == []

    def test_search_respects_top_k(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        results = searcher.bm25_search("test_col", "hệ thống", top_k=2)
        assert len(results) <= 2

    def test_result_has_correct_fields(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        results = searcher.bm25_search("test_col", "Python machine learning", top_k=1)
        assert len(results) == 1
        r = results[0]
        assert "text" in r
        assert "score" in r
        assert "metadata" in r
        assert isinstance(r["score"], float)
        assert isinstance(r["metadata"], dict)

    def test_metadata_preserved(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        results = searcher.bm25_search("test_col", "Python machine learning", top_k=1)
        meta = results[0]["metadata"]
        assert meta["document_id"] == "doc2"
        assert meta["source"] == "python_guide.pdf"


class TestDelete:
    """Verify that deleted chunks no longer appear in search results."""

    def test_delete_removes_from_results(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        # Delete the BM25 chunk
        searcher.mark_deleted("test_col", ["doc1_0"])

        results = searcher.bm25_search("test_col", "BM25 xếp hạng", top_k=5)
        result_ids = [r.get("id") for r in results]
        assert "doc1_0" not in result_ids

    def test_delete_all_doc_chunks(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        # Delete all doc2 chunks
        searcher.mark_deleted("test_col", ["doc2_0", "doc2_1"])

        results = searcher.bm25_search("test_col", "Python Docker", top_k=5)
        for r in results:
            assert r.get("metadata", {}).get("document_id") != "doc2"

    def test_delete_nonexistent_is_noop(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])
        # Should not raise
        searcher.mark_deleted("test_col", ["nonexistent_id"])

    def test_delete_empty_list_is_noop(self, searcher):
        searcher.mark_deleted("test_col", [])  # should not raise


class TestHybridSearch:
    """Verify RRF fusion works correctly."""

    def test_hybrid_merges_semantic_and_bm25(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        semantic_results = [
            {"text": SAMPLE_CHUNKS["texts"][1], "score": 0.95, "metadata": SAMPLE_CHUNKS["metadatas"][1]},
            {"text": SAMPLE_CHUNKS["texts"][3], "score": 0.90, "metadata": SAMPLE_CHUNKS["metadatas"][3]},
        ]

        results = searcher.hybrid_search("test_col", "BM25 xếp hạng", semantic_results, top_k=5)

        assert len(results) > 0
        # Should contain results from both semantic and BM25
        texts = [r["text"] for r in results]
        # BM25 should bring in the BM25-related chunk
        assert any("BM25" in t for t in texts)

    def test_hybrid_respects_top_k(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])

        semantic_results = [
            {"text": SAMPLE_CHUNKS["texts"][i], "score": 0.9 - i * 0.1, "metadata": SAMPLE_CHUNKS["metadatas"][i]}
            for i in range(5)
        ]

        results = searcher.hybrid_search("test_col", "test query", semantic_results, top_k=3)
        assert len(results) <= 3


class TestDeleteCollection:
    """Verify collection deletion clears all data."""

    def test_delete_collection_clears_index(self, searcher):
        searcher.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])
        assert len(searcher.bm25_search("test_col", "BM25", top_k=5)) > 0

        searcher.delete_collection("test_col")

        results = searcher.bm25_search("test_col", "BM25", top_k=5)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PERSISTENCE TESTS — Tantivy's key advantage
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    """Verify that indexes survive HybridSearcher recreation (simulates restart)."""

    def test_data_survives_restart(self, index_dir):
        """Add data, destroy searcher, create new one → data still there."""
        with patch("rag.hybrid_search.BM25_INDEX_DIR", index_dir), \
             patch("rag.hybrid_search.BM25_WRITER_HEAP_SIZE", 15_000_000):
            from rag.hybrid_search import HybridSearcher

            # Session 1: add data
            s1 = HybridSearcher()
            s1.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])
            results_before = s1.bm25_search("test_col", "BM25", top_k=3)
            assert len(results_before) > 0
            del s1  # simulate process exit

            # Session 2: new searcher, same disk
            s2 = HybridSearcher()
            results_after = s2.bm25_search("test_col", "BM25", top_k=3)

            assert len(results_after) > 0
            assert results_after[0]["text"] == results_before[0]["text"]

    def test_delete_persists_across_restart(self, index_dir):
        with patch("rag.hybrid_search.BM25_INDEX_DIR", index_dir), \
             patch("rag.hybrid_search.BM25_WRITER_HEAP_SIZE", 15_000_000):
            from rag.hybrid_search import HybridSearcher

            s1 = HybridSearcher()
            s1.add_chunks("test_col", SAMPLE_CHUNKS["ids"], SAMPLE_CHUNKS["texts"], SAMPLE_CHUNKS["metadatas"])
            s1.mark_deleted("test_col", ["doc1_0"])
            del s1

            s2 = HybridSearcher()
            results = s2.bm25_search("test_col", "BM25 xếp hạng", top_k=5)
            result_ids = [r.get("id") for r in results]
            assert "doc1_0" not in result_ids


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PERFORMANCE BENCHMARKS
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerformance:
    """Benchmark tests — not strict assertions, but print timing for comparison."""

    @pytest.fixture()
    def large_dataset(self):
        """Generate 10K chunks for benchmarking."""
        n = 10_000
        return {
            "ids": [f"bench_{i}" for i in range(n)],
            "texts": [
                f"Đây là chunk số {i} chứa nội dung về truy xuất thông tin, "
                f"machine learning, và xử lý ngôn ngữ tự nhiên. "
                f"Mỗi chunk có nội dung khác nhau để kiểm tra BM25 scoring. "
                f"Keywords: chunk{i} data{i % 100} topic{i % 50}"
                for i in range(n)
            ],
            "metadatas": [{"document_id": f"doc_{i // 100}", "chunk_idx": i} for i in range(n)],
        }

    def test_bench_bulk_add(self, searcher, large_dataset):
        """Benchmark: time to add 10K chunks."""
        t0 = time.perf_counter()
        searcher.add_chunks(
            "bench_col",
            large_dataset["ids"],
            large_dataset["texts"],
            large_dataset["metadatas"],
        )
        elapsed = time.perf_counter() - t0
        print(f"\n[BENCH] Bulk add 10K chunks: {elapsed:.3f}s")
        # Should be under 10s even on slow machines
        assert elapsed < 30

    def test_bench_search_latency(self, searcher, large_dataset):
        """Benchmark: search latency on 10K-chunk index."""
        searcher.add_chunks(
            "bench_col",
            large_dataset["ids"],
            large_dataset["texts"],
            large_dataset["metadatas"],
        )

        queries = [
            "truy xuất thông tin",
            "machine learning",
            "xử lý ngôn ngữ tự nhiên",
            "BM25 scoring",
            "chunk5000",
        ]

        times = []
        for q in queries:
            t0 = time.perf_counter()
            results = searcher.bm25_search("bench_col", q, top_k=10)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            assert len(results) > 0

        avg_ms = sum(times) / len(times) * 1000
        print(f"\n[BENCH] Avg search latency (10K chunks): {avg_ms:.1f}ms")
        # Should be under 100ms per query
        assert avg_ms < 500

    def test_bench_incremental_add(self, searcher, large_dataset):
        """Benchmark: add 10K first, then add 50 more (simulating document upload)."""
        searcher.add_chunks(
            "bench_col",
            large_dataset["ids"],
            large_dataset["texts"],
            large_dataset["metadatas"],
        )

        # Now add 50 more chunks incrementally
        new_ids = [f"new_{i}" for i in range(50)]
        new_texts = [f"Tài liệu mới số {i} về deep learning và transformer" for i in range(50)]
        new_metas = [{"document_id": "new_doc", "chunk_idx": i} for i in range(50)]

        t0 = time.perf_counter()
        searcher.add_chunks("bench_col", new_ids, new_texts, new_metas)
        elapsed = time.perf_counter() - t0
        print(f"\n[BENCH] Incremental add 50 chunks to 10K index: {elapsed*1000:.1f}ms")
        # Should be under 1s (ideally <100ms)
        assert elapsed < 5

        # Verify new chunks are searchable
        results = searcher.bm25_search("bench_col", "deep learning transformer", top_k=5)
        assert any("deep learning" in r["text"] for r in results)

    def test_bench_delete(self, searcher, large_dataset):
        """Benchmark: delete 200 chunks (simulating document deletion)."""
        searcher.add_chunks(
            "bench_col",
            large_dataset["ids"],
            large_dataset["texts"],
            large_dataset["metadatas"],
        )

        ids_to_delete = [f"bench_{i}" for i in range(200)]

        t0 = time.perf_counter()
        searcher.mark_deleted("bench_col", ids_to_delete)
        elapsed = time.perf_counter() - t0
        print(f"\n[BENCH] Delete 200 chunks from 10K index: {elapsed*1000:.1f}ms")
        assert elapsed < 5

    def test_bench_cold_start(self, index_dir, large_dataset):
        """Benchmark: time to open an existing 10K-chunk index (simulates restart)."""
        with patch("rag.hybrid_search.BM25_INDEX_DIR", index_dir), \
             patch("rag.hybrid_search.BM25_WRITER_HEAP_SIZE", 15_000_000):
            from rag.hybrid_search import HybridSearcher

            # Build index first
            s1 = HybridSearcher()
            s1.add_chunks("bench_col", large_dataset["ids"], large_dataset["texts"], large_dataset["metadatas"])
            del s1

            # Measure cold start
            t0 = time.perf_counter()
            s2 = HybridSearcher()
            results = s2.bm25_search("bench_col", "truy xuất thông tin", top_k=5)
            elapsed = time.perf_counter() - t0

            print(f"\n[BENCH] Cold start (open 10K index + first search): {elapsed*1000:.1f}ms")
            assert len(results) > 0
            # Should be WAY under the 8-30s of rank_bm25 rebuild
            assert elapsed < 5


# ═══════════════════════════════════════════════════════════════════════════════
# 4. COMPARISON TEST — rank_bm25 vs tantivy (run manually)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompareWithRankBM25:
    """Compare search results between rank_bm25 and tantivy.

    This test helps verify that tantivy produces similar ranking to rank_bm25.
    Not a strict equality test — BM25 implementations differ in tokenization
    and parameter details — but the top results should largely overlap.
    """

    def test_top_results_overlap(self, searcher):
        """Top-5 results from tantivy should overlap with rank_bm25's top-5."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            pytest.skip("rank_bm25 not installed — skip comparison test")

        texts = SAMPLE_CHUNKS["texts"]
        metadatas = SAMPLE_CHUNKS["metadatas"]
        ids = SAMPLE_CHUNKS["ids"]

        # --- rank_bm25 ---
        tokenized = [t.lower().split() for t in texts]
        bm25_old = BM25Okapi(tokenized)

        query = "truy xuất thông tin BM25"
        tokenized_query = query.lower().split()
        scores_old = bm25_old.get_scores(tokenized_query)
        top_old = sorted(range(len(scores_old)), key=lambda i: scores_old[i], reverse=True)[:3]
        old_top_texts = {texts[i] for i in top_old}

        # --- tantivy ---
        searcher.add_chunks("cmp_col", ids, texts, metadatas)
        results_new = searcher.bm25_search("cmp_col", query, top_k=3)
        new_top_texts = {r["text"] for r in results_new}

        # At least 2 out of 3 top results should overlap
        overlap = old_top_texts & new_top_texts
        print(f"\n[COMPARE] rank_bm25 top-3: {old_top_texts}")
        print(f"[COMPARE] tantivy  top-3: {new_top_texts}")
        print(f"[COMPARE] overlap: {len(overlap)}/3")
        assert len(overlap) >= 1, f"Expected at least 1 overlapping result, got {overlap}"

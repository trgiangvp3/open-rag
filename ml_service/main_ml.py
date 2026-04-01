"""Python ML Service — thin internal API for .NET orchestration.

Responsibilities:
  - /ml/convert   : file → markdown (MarkItDown)
  - /ml/index     : pre-chunked texts → embed + store (ChromaDB)
  - /ml/search    : query → embed + search ChromaDB
  - /ml/documents/delete
  - /ml/collections/ensure | delete
  - /ml/health
"""

import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from config import EMBEDDING_MODEL, ML_HOST, ML_PORT
from rag.converter import convert_to_markdown
from rag.embedder import get_embedder
from rag.hybrid_search import HybridSearcher
from rag.reranker import get_reranker
from rag.store import VectorStore
from schemas_ml import (
    BM25StatsResponse,
    ChunkResult as CR,
    CollectionRequest,
    DeleteDocumentRequest,
    DeleteDocumentResponse,
    HealthResponse,
    IndexRequest,
    IndexResponse,
    OkResponse,
    SearchRequest,
    SearchResponse,
    UpdateMetadataRequest,
    UpdateMetadataResponse,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

store: VectorStore
hybrid_searcher: HybridSearcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    global store, hybrid_searcher
    logger.info("Starting ML service — pre-loading embedding model...")
    get_embedder()
    # Reranker is lazy-loaded on first rerank request to save ~2 GB RAM
    store = VectorStore()
    hybrid_searcher = HybridSearcher()
    logger.info("ML service ready.")
    yield


app = FastAPI(title="OpenRAG ML Service", lifespan=lifespan)


# ── Convert ─────────────────────────────────────────────────────────────────

@app.post("/ml/convert")
async def convert_file(
    file: UploadFile = File(...),
    filename: str = Form(...),
):
    """Convert uploaded file to markdown using MarkItDown."""
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        loop = asyncio.get_event_loop()
        markdown = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: convert_to_markdown(tmp_path, filename)),
            timeout=120,
        )
        return {"markdown": markdown, "ok": True}
    except asyncio.TimeoutError:
        logger.error(f"Conversion timed out for '{filename}'")
        raise HTTPException(status_code=408, detail="File conversion timed out")
    except Exception as e:
        logger.error(f"Conversion failed for '{filename}': {e}")
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Index (embed + store pre-chunked texts) ──────────────────────────────────

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
    chunk_ids = store.add_chunks(
        collection_name=req.collection,
        document_id=req.document_id,
        texts=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    try:
        hybrid_searcher.add_chunks(
            collection_name=req.collection,
            chunk_ids=chunk_ids,
            texts=texts,
            metadatas=metadatas,
        )
    except Exception:
        # BM25 index failure is non-fatal — ChromaDB already has the data.
        # Use /ml/bm25/rebuild to recover later.
        logger.warning("BM25 index failed for doc %s, ChromaDB OK", req.document_id)

    return IndexResponse(document_id=req.document_id, chunk_count=len(texts))


# ── Embed text (for HyDE) ───────────────────────────────────────────────────

@app.post("/ml/embed")
async def embed_text(req: dict):
    """Embed a text string and search with the resulting vector."""
    text = req.get("text", "")
    col = req.get("collection", "documents")
    top_k = req.get("top_k", 5)
    use_reranker = req.get("use_reranker", False)
    search_mode = req.get("search_mode", "semantic")

    embedder = get_embedder()
    loop = asyncio.get_event_loop()
    query_embedding = await loop.run_in_executor(None, lambda: embedder.embed_query(text))

    semantic_top_k = top_k * 10 if (use_reranker or search_mode == "hybrid") else top_k
    semantic_results = store.search(collection_name=col, query_embedding=query_embedding, top_k=semantic_top_k)
    raw = semantic_results

    if search_mode == "hybrid":
        raw = await loop.run_in_executor(
            None, lambda: hybrid_searcher.hybrid_search(col, text, raw, top_k))

    if use_reranker:
        reranker = get_reranker()
        raw = await loop.run_in_executor(None, lambda: reranker.rerank(text, raw, top_k))
    elif search_mode != "hybrid":
        raw = raw[:top_k]

    results = [CR(text=r["text"], score=r["score"], rerank_score=r.get("rerank_score"), metadata=r["metadata"]) for r in raw]
    return SearchResponse(results=results, total=len(results))


# ── Search ───────────────────────────────────────────────────────────────────

@app.post("/ml/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """Embed query and search ChromaDB, with optional hybrid search and reranking."""
    embedder = get_embedder()
    loop = asyncio.get_event_loop()
    query_embedding = await loop.run_in_executor(
        None, lambda: embedder.embed_query(req.query)
    )

    # Determine how many semantic results to fetch before reranking / fusion
    semantic_top_k = req.top_k * 10 if (req.use_reranker or req.search_mode == "hybrid") else req.top_k

    semantic_results = store.search(
        collection_name=req.collection,
        query_embedding=query_embedding,
        top_k=semantic_top_k,
        where=req.metadata_filter,
    )

    # semantic_results is already list[dict] from store.search()
    raw = semantic_results

    if req.search_mode == "hybrid":
        raw = await loop.run_in_executor(
            None,
            lambda: hybrid_searcher.hybrid_search(req.collection, req.query, raw, req.top_k,
                                                   metadata_filter=req.metadata_filter),
        )

    if req.use_reranker:
        reranker = get_reranker()
        raw = await loop.run_in_executor(
            None,
            lambda: reranker.rerank(req.query, raw, req.top_k),
        )
    elif req.search_mode != "hybrid":
        raw = raw[:req.top_k]

    results = [CR(text=r["text"], score=r["score"], rerank_score=r.get("rerank_score"), metadata=r["metadata"]) for r in raw]
    return SearchResponse(results=results, total=len(results))


# ── Get document chunks ──────────────────────────────────────────────────────

@app.get("/ml/documents/{document_id}/chunks")
async def get_document_chunks(document_id: str, collection: str = "documents"):
    """Return all chunks for a document, ordered by chunk index."""
    try:
        col = store.client.get_collection(collection)
    except Exception:
        return {"document_id": document_id, "chunks": [], "total": 0}

    result = col.get(
        where={"document_id": document_id},
        include=["documents", "metadatas"],
    )
    ids = result.get("ids") or []
    texts = result.get("documents") or []
    metadatas = result.get("metadatas") or []

    chunks = []
    for cid, text, meta in zip(ids, texts, metadatas):
        chunks.append({"id": cid, "text": text, "metadata": meta})
    chunks.sort(key=lambda c: c["id"])
    return {"document_id": document_id, "chunks": chunks, "total": len(chunks)}


# ── Delete document ──────────────────────────────────────────────────────────

@app.post("/ml/documents/delete", response_model=DeleteDocumentResponse)
async def delete_document(req: DeleteDocumentRequest):
    """Delete all chunks of a document from ChromaDB."""
    deleted_count, deleted_ids = store.delete_document(req.collection, req.document_id)
    try:
        hybrid_searcher.mark_deleted(collection_name=req.collection, chunk_ids=deleted_ids)
    except Exception:
        logger.warning("BM25 delete failed for doc %s, ChromaDB OK", req.document_id)
    return DeleteDocumentResponse(chunks_deleted=deleted_count)


# ── Update document metadata ─────────────────────────────────────────────────

@app.post("/ml/documents/update-metadata", response_model=UpdateMetadataResponse)
async def update_document_metadata(req: UpdateMetadataRequest):
    """Update metadata on all chunks of a document without re-embedding."""
    count, chunk_ids, texts, new_metadatas = store.update_document_metadata(
        req.collection, req.document_id, req.metadata_updates
    )
    if chunk_ids:
        try:
            hybrid_searcher.get_or_create_index(req.collection).add(chunk_ids, texts, new_metadatas)
        except Exception:
            logger.warning("BM25 metadata update failed for doc %s, ChromaDB OK", req.document_id)
    return UpdateMetadataResponse(chunks_updated=count)


# ── Collections ──────────────────────────────────────────────────────────────

@app.post("/ml/collections/ensure", response_model=OkResponse)
async def ensure_collection(req: CollectionRequest):
    """Ensure a ChromaDB collection exists."""
    store.get_or_create_collection(req.name)
    return OkResponse()


@app.post("/ml/collections/delete", response_model=OkResponse)
async def delete_collection(req: CollectionRequest):
    """Delete a ChromaDB collection."""
    try:
        store.delete_collection(req.name)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    hybrid_searcher.delete_collection(req.name)
    return OkResponse()


# ── BM25 management ──────────────────────────────────────────────────────

@app.post("/ml/bm25/rebuild", response_model=OkResponse)
async def bm25_rebuild(req: CollectionRequest):
    """Rebuild the BM25 index for a collection from ChromaDB data."""
    collection = store.get_or_create_collection(req.name)
    result = collection.get(include=["documents", "metadatas"])
    ids: list[str] = result.get("ids") or []
    texts: list[str] = result.get("documents") or []
    metadatas: list[dict] = result.get("metadatas") or []

    hybrid_searcher.delete_collection(req.name)
    if ids:
        hybrid_searcher.add_chunks(
            collection_name=req.name,
            chunk_ids=ids,
            texts=texts,
            metadatas=metadatas,
        )
    return OkResponse()


@app.get("/ml/bm25/stats", response_model=BM25StatsResponse)
async def bm25_stats():
    """Return BM25 index statistics for all collections."""
    return BM25StatsResponse(collections=hybrid_searcher.stats())


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/ml/health", response_model=HealthResponse)
async def health():
    embedder = get_embedder()
    return HealthResponse(ok=True, model=EMBEDDING_MODEL, device=embedder.device)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_ml:app", host=ML_HOST, port=ML_PORT, reload=False)

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
    get_reranker()
    store = VectorStore()
    hybrid_searcher = HybridSearcher(store)
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
    store.add_chunks(
        collection_name=req.collection,
        document_id=req.document_id,
        texts=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    hybrid_searcher.invalidate(req.collection)

    return IndexResponse(document_id=req.document_id, chunk_count=len(texts))


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
    )

    # Convert to plain dicts for hybrid / reranker processing
    raw = [{"text": r.text, "score": r.score, "metadata": r.metadata} for r in semantic_results]

    if req.search_mode == "hybrid":
        raw = await loop.run_in_executor(
            None,
            lambda: hybrid_searcher.hybrid_search(req.collection, req.query, raw, req.top_k),
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


# ── Delete document ──────────────────────────────────────────────────────────

@app.post("/ml/documents/delete", response_model=DeleteDocumentResponse)
async def delete_document(req: DeleteDocumentRequest):
    """Delete all chunks of a document from ChromaDB."""
    deleted = store.delete_document(req.collection, req.document_id)
    hybrid_searcher.invalidate(req.collection)
    return DeleteDocumentResponse(chunks_deleted=deleted)


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
    return OkResponse()


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/ml/health", response_model=HealthResponse)
async def health():
    embedder = get_embedder()
    return HealthResponse(ok=True, model=EMBEDDING_MODEL, device=embedder.device)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_ml:app", host=ML_HOST, port=ML_PORT, reload=False)

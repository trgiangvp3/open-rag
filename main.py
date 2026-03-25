"""OpenRAG - Document Search API powered by bge-m3 + ChromaDB."""

import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config import BASE_DIR, DEFAULT_TOP_K, HOST, PORT
from rag.store import VectorStore
from rag.indexer import Indexer
from rag.retriever import Retriever
from schemas import (
    CollectionCreate,
    CollectionInfo,
    DocumentListResponse,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    StatusResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- Globals ---
store: VectorStore
indexer: Indexer
retriever: Retriever


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize components on startup."""
    global store, indexer, retriever

    logger.info("Initializing OpenRAG...")
    store = VectorStore()
    indexer = Indexer(store)
    retriever = Retriever(store)

    # Pre-load embedding model in background
    logger.info("Pre-loading embedding model (this may take a minute on first run)...")
    from rag.embedder import get_embedder
    get_embedder()
    logger.info("OpenRAG ready!")

    yield

    logger.info("Shutting down OpenRAG.")


app = FastAPI(
    title="OpenRAG",
    description="Document Search API - Upload, index, and search documents using semantic similarity.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== Health ==========

@app.get("/api/health", response_model=StatusResponse)
async def health():
    return StatusResponse(status="ok", message="OpenRAG is running")


# ========== Document Ingestion ==========

@app.post("/api/documents/upload", response_model=IngestResponse)
async def upload_document(
    file: UploadFile = File(...),
    collection: str = Form("documents"),
):
    """Upload and index a document."""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    # Save to temp file then ingest
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = await indexer.ingest_file(tmp_path, file.filename, collection)
        return IngestResponse(**result)
    except Exception as e:
        logger.exception("Ingestion failed")
        raise HTTPException(500, f"Ingestion failed: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/documents/text", response_model=IngestResponse)
async def ingest_text(
    text: str = Form(...),
    title: str = Form("untitled"),
    collection: str = Form("documents"),
):
    """Index raw text/markdown directly."""
    try:
        result = await indexer.ingest_text(text, title, collection)
        return IngestResponse(**result)
    except Exception as e:
        logger.exception("Text ingestion failed")
        raise HTTPException(500, f"Ingestion failed: {e}")


# ========== Search ==========

@app.post("/api/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """Semantic search across indexed documents."""
    results = retriever.search(
        query=req.query,
        collection=req.collection,
        top_k=req.top_k,
    )
    return SearchResponse(
        query=req.query,
        results=results,
        total=len(results),
    )


# ========== Documents Management ==========

@app.get("/api/documents", response_model=DocumentListResponse)
async def list_documents(collection: str = "documents"):
    """List all indexed documents in a collection."""
    docs = store.list_documents(collection)
    return DocumentListResponse(documents=docs, total=len(docs))


@app.delete("/api/documents/{document_id}", response_model=StatusResponse)
async def delete_document(document_id: str, collection: str = "documents"):
    """Delete a document and all its chunks."""
    deleted = store.delete_document(collection, document_id)
    if deleted == 0:
        raise HTTPException(404, "Document not found")
    return StatusResponse(
        status="ok",
        message=f"Deleted {deleted} chunks",
        details={"document_id": document_id, "chunks_deleted": deleted},
    )


# ========== Collections ==========

@app.get("/api/collections", response_model=list[CollectionInfo])
async def list_collections():
    """List all collections."""
    return store.list_collections()


@app.post("/api/collections", response_model=StatusResponse)
async def create_collection(req: CollectionCreate):
    """Create a new collection."""
    store.get_or_create_collection(req.name)
    return StatusResponse(status="ok", message=f"Collection '{req.name}' created")


@app.delete("/api/collections/{name}", response_model=StatusResponse)
async def delete_collection(name: str):
    """Delete a collection and all its data."""
    try:
        store.delete_collection(name)
        return StatusResponse(status="ok", message=f"Collection '{name}' deleted")
    except Exception:
        raise HTTPException(404, f"Collection '{name}' not found")


# ========== Frontend ==========

@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ========== Run ==========

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)

"""Internal Pydantic schemas for ML service API."""

from pydantic import BaseModel


# ── /ml/index ──────────────────────────────────────────────────────────────

class ChunkInput(BaseModel):
    text: str
    metadata: dict = {}


class IndexRequest(BaseModel):
    document_id: str
    collection: str
    chunks: list[ChunkInput]


class IndexResponse(BaseModel):
    document_id: str
    chunk_count: int
    ok: bool = True


# ── /ml/search ─────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    collection: str
    top_k: int = 5
    use_reranker: bool = False
    search_mode: str = "semantic"  # "semantic" | "hybrid"


class ChunkResult(BaseModel):
    text: str
    score: float
    rerank_score: float | None = None
    metadata: dict


class SearchResponse(BaseModel):
    results: list[ChunkResult]
    total: int


# ── /ml/documents/delete ───────────────────────────────────────────────────

class DeleteDocumentRequest(BaseModel):
    document_id: str
    collection: str


class DeleteDocumentResponse(BaseModel):
    chunks_deleted: int
    ok: bool = True


# ── /ml/collections/* ──────────────────────────────────────────────────────

class CollectionRequest(BaseModel):
    name: str


class OkResponse(BaseModel):
    ok: bool = True


# ── /ml/bm25/* ─────────────────────────────────────────────────────────────

class BM25StatsEntry(BaseModel):
    doc_count: int
    index_path: str


class BM25StatsResponse(BaseModel):
    collections: dict[str, BM25StatsEntry]


# ── /ml/health ─────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    ok: bool
    model: str
    device: str

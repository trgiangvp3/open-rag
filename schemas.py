"""Pydantic models for API request/response."""

from typing import Optional

from pydantic import BaseModel


# --- Requests ---

class SearchRequest(BaseModel):
    query: str
    collection: str = "documents"
    top_k: int = 5


class CollectionCreate(BaseModel):
    name: str
    description: str = ""


# --- Responses ---

class ChunkResult(BaseModel):
    text: str
    score: float
    metadata: dict


class SearchResponse(BaseModel):
    query: str
    results: list[ChunkResult]
    total: int


class DocumentInfo(BaseModel):
    id: str
    filename: str
    collection: str
    chunk_count: int
    created_at: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentInfo]
    total: int


class CollectionInfo(BaseModel):
    name: str
    description: str
    document_count: int
    chunk_count: int


class IngestResponse(BaseModel):
    document_id: str
    filename: str
    chunk_count: int
    message: str


class StatusResponse(BaseModel):
    status: str
    message: str
    details: Optional[dict] = None

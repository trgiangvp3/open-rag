"""Application configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
CONVERTED_DIR = DATA_DIR / "converted"
CHROMA_DIR = DATA_DIR / "chroma"

# Ensure directories exist
for d in [UPLOAD_DIR, CONVERTED_DIR, CHROMA_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Embedding model
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")  # auto | cpu | cuda

# ChromaDB
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "documents")

# Chunking
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "150"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "20"))

# Search
DEFAULT_TOP_K = int(os.getenv("DEFAULT_TOP_K", "5"))

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

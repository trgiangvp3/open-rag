"""ML service configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Data directory is shared with the .NET service — one level up from ml_service/
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = DATA_DIR / "chroma"
UPLOAD_DIR = DATA_DIR / "uploads"

for d in [CHROMA_DIR, UPLOAD_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Embedding model
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")  # auto | cpu | cuda

# Reranker
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")

# ChromaDB
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "documents")

# Server
ML_HOST = os.getenv("ML_HOST", "0.0.0.0")
ML_PORT = int(os.getenv("ML_PORT", "8001"))

"""Ingestion pipeline: file → convert → chunk → embed → store."""

import asyncio
import logging
import shutil
import uuid
from functools import partial
from pathlib import Path

from markitdown import MarkItDown

from config import CONVERTED_DIR, UPLOAD_DIR
from rag.chunker import MarkdownChunker
from rag.embedder import get_embedder
from rag.store import VectorStore

logger = logging.getLogger(__name__)


class Indexer:
    """Ingest documents into the vector store."""

    def __init__(self, store: VectorStore):
        self.store = store
        self.chunker = MarkdownChunker()

    async def ingest_file(
        self,
        file_path: Path,
        original_filename: str,
        collection: str = "documents",
    ) -> dict:
        document_id = str(uuid.uuid4())

        # 1. Save uploaded file
        saved_path = UPLOAD_DIR / f"{document_id}_{original_filename}"
        shutil.copy2(file_path, saved_path)
        logger.info(f"Saved file: {saved_path}")

        # 2. Convert to markdown via markitdown-service
        markdown = await self._convert_to_markdown(saved_path, original_filename)

        # 3. Save converted markdown
        md_path = CONVERTED_DIR / f"{document_id}.md"
        md_path.write_text(markdown, encoding="utf-8")

        # 4. Chunk
        chunks = self.chunker.chunk(markdown, metadata={"filename": original_filename})

        if not chunks:
            return {
                "document_id": document_id,
                "filename": original_filename,
                "chunk_count": 0,
                "message": "No content extracted from document",
            }

        # 5. Embed (run in thread pool to avoid blocking async loop)
        embedder = get_embedder()
        texts = [c.text for c in chunks]
        logger.info(f"Embedding {len(texts)} chunks for '{original_filename}'...")
        embeddings = await asyncio.get_event_loop().run_in_executor(
            None, partial(embedder.embed_texts, texts)
        )

        # 6. Store
        metadatas = [
            {**c.metadata, "chunk_index": c.index}
            for c in chunks
        ]
        self.store.add_chunks(
            collection_name=collection,
            texts=texts,
            embeddings=embeddings,
            metadatas=metadatas,
            document_id=document_id,
        )

        return {
            "document_id": document_id,
            "filename": original_filename,
            "chunk_count": len(chunks),
            "message": f"Indexed {len(chunks)} chunks",
        }

    async def ingest_text(
        self,
        text: str,
        title: str = "untitled",
        collection: str = "documents",
    ) -> dict:
        document_id = str(uuid.uuid4())

        # Save raw text
        md_path = CONVERTED_DIR / f"{document_id}.md"
        md_path.write_text(text, encoding="utf-8")

        # Chunk → Embed → Store
        chunks = self.chunker.chunk(text, metadata={"filename": title})

        if not chunks:
            return {
                "document_id": document_id,
                "filename": title,
                "chunk_count": 0,
                "message": "No content to index",
            }

        embedder = get_embedder()
        texts = [c.text for c in chunks]
        logger.info(f"Embedding {len(texts)} chunks for '{title}'...")
        embeddings = await asyncio.get_event_loop().run_in_executor(
            None, partial(embedder.embed_texts, texts)
        )
        metadatas = [{**c.metadata, "chunk_index": c.index} for c in chunks]

        self.store.add_chunks(
            collection_name=collection,
            texts=texts,
            embeddings=embeddings,
            metadatas=metadatas,
            document_id=document_id,
        )

        return {
            "document_id": document_id,
            "filename": title,
            "chunk_count": len(chunks),
            "message": f"Indexed {len(chunks)} chunks",
        }

    _markitdown = MarkItDown()

    async def _convert_to_markdown(self, file_path: Path, filename: str) -> str:
        """Convert file to markdown using markitdown."""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: self._markitdown.convert(str(file_path))
        )
        logger.info(f"Converted '{filename}' to markdown ({len(result.text_content)} chars)")
        return result.text_content

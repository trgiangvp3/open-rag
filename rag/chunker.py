"""Text chunking module for splitting documents into smaller pieces."""

import logging
import re
from dataclasses import dataclass

from config import CHUNK_OVERLAP, CHUNK_SIZE

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    text: str
    index: int
    metadata: dict


class MarkdownChunker:
    """Split markdown text into chunks, respecting headers and paragraphs."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str, metadata: dict | None = None) -> list[Chunk]:
        metadata = metadata or {}
        sections = self._split_by_sections(text)
        chunks = []

        for section in sections:
            section_chunks = self._split_section(section["text"])
            for i, chunk_text in enumerate(section_chunks):
                chunk_text = chunk_text.strip()
                if not chunk_text:
                    continue
                chunk_meta = {**metadata}
                if section["header"]:
                    chunk_meta["section"] = section["header"]
                chunks.append(Chunk(
                    text=chunk_text,
                    index=len(chunks),
                    metadata=chunk_meta,
                ))

        logger.info(f"Split text ({len(text)} chars) into {len(chunks)} chunks")
        return chunks

    def _split_by_sections(self, text: str) -> list[dict]:
        header_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
        sections = []
        last_end = 0
        last_header = ""

        for match in header_pattern.finditer(text):
            if match.start() > last_end:
                content = text[last_end:match.start()]
                if content.strip():
                    sections.append({"header": last_header, "text": content})
            last_header = match.group(2).strip()
            last_end = match.end()

        # Remaining text
        remaining = text[last_end:]
        if remaining.strip():
            sections.append({"header": last_header, "text": remaining})

        if not sections:
            sections.append({"header": "", "text": text})

        return sections

    def _split_section(self, text: str) -> list[str]:
        words = text.split()
        if len(words) <= self.chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(words):
            end = start + self.chunk_size
            chunk = " ".join(words[start:end])
            chunks.append(chunk)
            start = end - self.chunk_overlap

        return chunks

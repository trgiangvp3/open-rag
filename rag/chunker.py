"""Text chunking module for splitting documents into semantic pieces."""

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


# Patterns for metadata sections that should be excluded from search indexing
_METADATA_PATTERNS = [
    re.compile(r"\*?\*?PH[AÂ]N PH[OỐ]I\*?\*?", re.IGNORECASE),
    re.compile(r"\*?\*?S[UỬ]A [DĐ][OỔ]I\*?\*?", re.IGNORECASE),
    re.compile(r"\*?\*?THEO D[OÕ]I S[UỬ]A [DĐ][OỔ]I\*?\*?", re.IGNORECASE),
    re.compile(r"\*?\*?SO[AẠ]N TH[AẢ]O\*?\*?", re.IGNORECASE),
    re.compile(r"\*?\*?PH[EÊ] DUY[EỆ]T\*?\*?", re.IGNORECASE),
]

# Header patterns: markdown # and bold **HEADER**
_HEADER_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_HEADER_PATTERN = re.compile(r"^\*\*(\d+[\.\)]\s*.+?)\*\*\s*$", re.MULTILINE)

# Table row pattern
_TABLE_ROW_PATTERN = re.compile(r"^\|.+\|$", re.MULTILINE)


class MarkdownChunker:
    """Split markdown text into semantic chunks, respecting document structure."""

    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str, metadata: dict | None = None) -> list[Chunk]:
        metadata = metadata or {}

        # 1. Strip metadata sections (revision history, approval tables, etc.)
        text = self._strip_metadata_sections(text)

        # 2. Split by headers (both # headers and **bold** headers)
        sections = self._split_by_sections(text)

        # 3. Within each section, split semantically (by paragraph/table/list)
        chunks = []
        for section in sections:
            header_path = section["header"]
            section_chunks = self._split_section_semantic(section["text"])

            for chunk_text in section_chunks:
                chunk_text = chunk_text.strip()
                if not chunk_text or len(chunk_text) < 20:
                    continue

                # Prepend header context to chunk for better embedding
                if header_path:
                    full_text = f"{header_path}\n\n{chunk_text}"
                else:
                    full_text = chunk_text

                chunk_meta = {**metadata}
                if header_path:
                    chunk_meta["section"] = header_path

                chunks.append(Chunk(
                    text=full_text,
                    index=len(chunks),
                    metadata=chunk_meta,
                ))

        logger.info(f"Split text ({len(text)} chars) into {len(chunks)} chunks")
        return chunks

    def _strip_metadata_sections(self, text: str) -> str:
        """Remove document metadata (revision tables, approvals) that adds noise."""
        lines = text.split("\n")
        result = []
        skip_until_blank = False
        skip_table = False

        for line in lines:
            # Check if line matches a metadata header
            if any(p.search(line) for p in _METADATA_PATTERNS):
                skip_until_blank = True
                skip_table = True
                continue

            # Skip table rows after metadata header
            if skip_table:
                if _TABLE_ROW_PATTERN.match(line.strip()) or line.strip().startswith("|") or line.strip() == "":
                    if line.strip() == "" and not _TABLE_ROW_PATTERN.match(line.strip()):
                        # End of table
                        skip_table = False
                        skip_until_blank = False
                    continue
                else:
                    skip_table = False
                    skip_until_blank = False

            if not skip_until_blank:
                result.append(line)

        return "\n".join(result)

    def _split_by_sections(self, text: str) -> list[dict]:
        """Split by markdown headers AND bold numbered headers (e.g., **1. Quy định**)."""
        # Collect all header positions
        headers = []

        for match in _HEADER_PATTERN.finditer(text):
            headers.append({
                "start": match.start(),
                "end": match.end(),
                "header": match.group(2).strip(),
                "level": len(match.group(1)),
            })

        for match in _BOLD_HEADER_PATTERN.finditer(text):
            headers.append({
                "start": match.start(),
                "end": match.end(),
                "header": match.group(1).strip(),
                "level": 2,
            })

        # Sort by position
        headers.sort(key=lambda h: h["start"])

        if not headers:
            return [{"header": "", "text": text}]

        # Build header path stack for hierarchical context
        sections = []
        header_stack = []

        for i, hdr in enumerate(headers):
            # Determine content range
            content_start = hdr["end"]
            content_end = headers[i + 1]["start"] if i + 1 < len(headers) else len(text)
            content = text[content_start:content_end].strip()

            # Maintain header hierarchy stack
            while header_stack and header_stack[-1]["level"] >= hdr["level"]:
                header_stack.pop()
            header_stack.append(hdr)

            # Build full path: "Parent > Child > Current"
            header_path = " > ".join(h["header"] for h in header_stack)

            if content:
                sections.append({"header": header_path, "text": content})

        # Content before first header
        first_content = text[:headers[0]["start"]].strip()
        if first_content:
            sections.insert(0, {"header": "", "text": first_content})

        return sections

    def _split_section_semantic(self, text: str) -> list[str]:
        """Split a section by paragraphs, tables, and lists — not by word count."""
        # Split into semantic blocks: paragraphs separated by blank lines
        blocks = self._extract_blocks(text)

        # Merge small blocks together, split large blocks
        chunks = []
        current = ""

        for block in blocks:
            block_words = len(block.split())

            # If single block exceeds limit, split it by sentences
            if block_words > self.chunk_size:
                if current.strip():
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(self._split_by_sentences(block))
                continue

            # If adding this block exceeds limit, flush current
            combined_words = len((current + "\n\n" + block).split())
            if combined_words > self.chunk_size and current.strip():
                chunks.append(current.strip())
                # Overlap: keep last part of current
                overlap_text = self._get_overlap_text(current)
                current = overlap_text + "\n\n" + block if overlap_text else block
            else:
                current = (current + "\n\n" + block).strip() if current else block

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def _extract_blocks(self, text: str) -> list[str]:
        """Extract semantic blocks: paragraphs, complete tables, complete lists."""
        lines = text.split("\n")
        blocks = []
        current_block = []
        in_table = False

        for line in lines:
            stripped = line.strip()

            # Table handling: keep table rows together
            if _TABLE_ROW_PATTERN.match(stripped) or (stripped.startswith("|") and stripped.endswith("|")):
                if not in_table and current_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                in_table = True
                current_block.append(line)
                continue

            if in_table and not stripped:
                # End of table
                blocks.append("\n".join(current_block))
                current_block = []
                in_table = False
                continue

            if in_table and stripped:
                # Separator line (---|---) is part of table
                if re.match(r"^\|[\s\-:]+\|$", stripped):
                    current_block.append(line)
                    continue
                else:
                    blocks.append("\n".join(current_block))
                    current_block = []
                    in_table = False

            # Blank line = paragraph separator
            if not stripped:
                if current_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                continue

            current_block.append(line)

        if current_block:
            blocks.append("\n".join(current_block))

        return [b.strip() for b in blocks if b.strip()]

    def _split_by_sentences(self, text: str) -> list[str]:
        """Split long text by sentence boundaries."""
        # Vietnamese sentence endings: . ! ? and newlines
        sentences = re.split(r'(?<=[.!?])\s+|\n+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        chunks = []
        current = ""

        for sent in sentences:
            combined = (current + " " + sent).strip() if current else sent
            if len(combined.split()) > self.chunk_size and current:
                chunks.append(current)
                current = sent
            else:
                current = combined

        if current:
            chunks.append(current)

        return chunks

    def _get_overlap_text(self, text: str) -> str:
        """Get the last N words of text for overlap."""
        if self.chunk_overlap <= 0:
            return ""
        words = text.split()
        if len(words) <= self.chunk_overlap:
            return text
        return " ".join(words[-self.chunk_overlap:])

"""File-to-markdown conversion using MarkItDown."""

import logging
from pathlib import Path

from markitdown import MarkItDown

logger = logging.getLogger(__name__)

_markitdown = MarkItDown()


def convert_to_markdown(file_path: Path, filename: str) -> str:
    """Convert any supported file to markdown text."""
    result = _markitdown.convert(str(file_path))
    logger.info(f"Converted '{filename}' ({len(result.text_content)} chars)")
    return result.text_content

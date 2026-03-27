"""File-to-markdown conversion using MarkItDown."""

import logging
from pathlib import Path

from markdownify import markdownify
from markitdown import MarkItDown

logger = logging.getLogger(__name__)

_markitdown = MarkItDown()

_HTML_EXTENSIONS = {".html", ".htm"}


def convert_to_markdown(file_path: Path, filename: str) -> str:
    """Convert any supported file to markdown text."""
    if file_path.suffix.lower() in _HTML_EXTENSIONS:
        html = file_path.read_text(encoding="utf-8", errors="replace")
        content = markdownify(html)
    else:
        result = _markitdown.convert(str(file_path))
        content = result.text_content

    logger.info(f"Converted '{filename}' ({len(content)} chars)")
    return content

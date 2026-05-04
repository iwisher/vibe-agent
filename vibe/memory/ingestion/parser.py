import logging
from pathlib import Path

try:
    from docling.document_converter import DocumentConverter
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False

logger = logging.getLogger(__name__)

class DocumentParser:
    """Parses various document formats into markdown using IBM Docling."""

    def __init__(self):
        if not DOCLING_AVAILABLE:
            logger.warning("docling is not installed. Native markdown parsing only.")
            self.converter = None
        else:
            self.converter = DocumentConverter()

    def parse(self, file_path: Path | str) -> str:
        """Parse a document and return its markdown content."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        # If docling is not available, we can only do best-effort text read if it's markdown
        if not DOCLING_AVAILABLE:
            if path.suffix.lower() in [".md", ".txt"]:
                return path.read_text(encoding="utf-8")
            raise RuntimeError(f"Cannot parse {path.suffix} without docling installed.")

        # Use Docling for all supported formats (PDF, DOCX, PPTX, HTML, MD, etc.)
        logger.info(f"Parsing document with Docling: {path}")
        result = self.converter.convert(path)
        return result.document.export_to_markdown()

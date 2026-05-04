import asyncio
import logging
from pathlib import Path
from typing import Any

from vibe.memory.ingestion.parser import DocumentParser
from vibe.memory.ingestion.chunker import SemanticChunker

logger = logging.getLogger(__name__)

class IngestionWorker:
    """Orchestrates the ingestion of files into the KnowledgeExtractor."""

    def __init__(self, extractor: Any, max_tokens: int = 4000):
        self.extractor = extractor
        self.parser = DocumentParser()
        self.chunker = SemanticChunker(max_tokens=max_tokens)
        self.semaphore = asyncio.Semaphore(3)

    async def _process_chunk(self, chunk: str, source_path: Path, index: int) -> int:
        """Process a single chunk via the KnowledgeExtractor."""
        async with self.semaphore:
            # We don't have a direct "add_chunk" in KnowledgeExtractor, but we can
            # simulate passing it as raw text or let the extractor treat it as a task.
            # In Vibe Agent, KnowledgeExtractor typically extracts from raw text 
            # and uses update_page to save it. 
            
            # Since extractor has an `extract_from_text` method (or similar)
            if hasattr(self.extractor, "extract_from_text"):
                pages_created = await self.extractor.extract_from_text(
                    text=chunk,
                    source=str(source_path),
                    metadata={"chunk_index": index}
                )
                return len(pages_created) if isinstance(pages_created, list) else pages_created
            elif hasattr(self.extractor, "wiki"):
                # Fallback: Just write the chunk directly as a draft Wiki page
                slug = f"{source_path.stem.lower()}-chunk-{index}"
                page = await self.extractor.wiki.update_page(
                    slug=slug,
                    content=chunk,
                    status="draft",
                    metadata={"source": str(source_path), "chunk_index": index, "type": "document"}
                )
                return 1
            return 0

    async def ingest_file(self, file_path: str | Path) -> int:
        """Ingest a single file, chunk it, and save to memory. Returns number of pages created."""
        path = Path(file_path)
        
        # 1. Parse using Docling (or raw if MD)
        logger.info(f"Starting ingestion for {path.name}...")
        try:
            markdown_text = self.parser.parse(path)
        except Exception as e:
            logger.error(f"Failed to parse {path.name}: {e}")
            raise RuntimeError(f"Ingestion failed during parsing: {e}")

        # 2. Semantic Chunking
        chunks = self.chunker.chunk(markdown_text)
        logger.info(f"Broke {path.name} into {len(chunks)} chunks.")

        # 3. Concurrent Extraction
        tasks = [
            self._process_chunk(chunk, path, i)
            for i, chunk in enumerate(chunks)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4. Tally results
        total_created = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"Chunk {i} failed: {r}")
            else:
                total_created += r

        logger.info(f"Ingestion complete for {path.name}. Created {total_created} Wiki Pages.")
        return total_created

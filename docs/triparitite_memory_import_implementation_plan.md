# Goal: Phase 3.6 Bulk Knowledge Ingestion (PDF/MD)

Implement a robust pipeline to ingest large documents (.pdf and .md) directly into the Tripartite Memory System. The system will leverage IBM Docling to convert PDFs natively to Markdown, semantically chunk the files to respect token limits, and pipe them directly into the `KnowledgeExtractor` to create formatted Wiki Pages.

## Open Questions
- Is there a specific default token limit you prefer for the chunk size? (The plan defaults to 4,000 tokens per chunk).
- Do you want the ingested pages to have a specific YAML frontmatter tag automatically applied (e.g., `source: import`, `type: document`)?

## Architecture & Components

### `vibe.memory.ingestion.DoclingParser`
- A wrapper around `docling.document_converter.DocumentConverter`.
- Extracts `.pdf` files into a native node tree (`DoclingDocument`).
- Provides a fallback method to read raw `.md` files directly.

### `vibe.memory.ingestion.SemanticChunker`
- **For PDFs:** Iterates over Docling document nodes (paragraphs, tables, lists). It aggregates these nodes into Markdown chunks, ensuring no chunk exceeds the 4,000 token limit without breaking a node mid-way.
- **For MDs:** Splits by Markdown headers (`#`, `##`) to preserve logical section flow, rolling over when reaching the token limit.

### `vibe.memory.ingestion.IngestionWorker`
- The orchestration layer.
- Takes the file path, determines the type (pdf/md), invokes the respective parser/chunker.
- Pipes the resulting chunks to the `KnowledgeExtractor`.
- Uses `asyncio.gather` bounded by a semaphore to write the resulting Wiki Pages concurrently.

### `vibe.cli.memory.import_cmd`
- Exposes `vibe memory import <path> --type [pdf|md]`.
- Uses a `rich` progress spinner to indicate parsing status.

## Proposed Changes

### `pyproject.toml`
#### [MODIFY] `pyproject.toml`
- Add `docling` to the core project dependencies.

### Ingestion Module
#### [NEW] `vibe/memory/ingestion/__init__.py`
#### [NEW] `vibe/memory/ingestion/parser.py`
- Contains `DoclingParser` and raw Markdown reader.
#### [NEW] `vibe/memory/ingestion/chunker.py`
- Contains `SemanticChunker` for both node-based and header-based chunking.
#### [NEW] `vibe/memory/ingestion/worker.py`
- Orchestrates the flow from parsing -> chunking -> `KnowledgeExtractor`.

### CLI Integration
#### [MODIFY] `vibe/cli/memory.py`
- Add the `import` command with `--type` options and `rich` progress bars.

## Verification Plan

### Automated Tests
- Create `tests/memory/test_ingestion.py`
- Mock the Docling parser to return a dummy `DoclingDocument`.
- Assert that `SemanticChunker` correctly clusters nodes within token limits.
- Assert that pure `.md` files are chunked by headers.

### Manual Verification
- Run `vibe memory import sample.pdf --type pdf` on a real PDF.
- Run `vibe memory import sample.md --type md` on a large MD file.
- Check `vibe memory wiki list` to verify the new Wiki Pages were successfully generated and structurally sound.

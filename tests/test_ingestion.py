import pytest
from pathlib import Path
from vibe.memory.ingestion.chunker import SemanticChunker

def test_semantic_chunker():
    chunker = SemanticChunker(max_tokens=20) # 80 chars max
    
    # Should keep short sections whole
    text1 = "# Header\n\nShort text."
    chunks1 = chunker.chunk(text1)
    assert len(chunks1) == 1
    
    # Should split over chunks if longer
    text2 = "# Section 1\n\nThis is a long sentence that exceeds forty characters easily.\n\n## Section 2\n\nAnother short one."
    chunks2 = chunker.chunk(text2)
    # Paragraph 2 is 61 chars, total is about 120. It should fit into 2 chunks
    assert len(chunks2) == 2
    assert "Section 1" in chunks2[0]
    assert "Another short" in chunks2[1]

def test_semantic_chunker_massive_block():
    chunker = SemanticChunker(max_tokens=10) # 40 chars max
    # Even without headers, it should split by paragraph.
    # 50 chars will be split into 40 + 10.
    text = "A" * 50 + "\n\n" + "B" * 50
    chunks = chunker.chunk(text)
    assert len(chunks) == 4
    assert chunks[0] == "A" * 40
    assert chunks[1] == "A" * 10
    assert chunks[2] == "B" * 40
    assert chunks[3] == "B" * 10

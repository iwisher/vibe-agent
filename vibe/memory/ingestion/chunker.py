import re

class SemanticChunker:
    """Chunks Markdown text to avoid exceeding token limits."""

    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens
        self.chars_per_token = 4
        self.max_chars = self.max_tokens * self.chars_per_token

    def chunk(self, markdown_text: str) -> list[str]:
        """Split markdown into semantic chunks bounded by token limits."""
        # Simple paragraph splitting
        paragraphs = re.split(r'\n\n+', markdown_text.strip())
        
        chunks = []
        current_chunk = ""
        
        for p in paragraphs:
            p_len = len(p)
            
            # If a single paragraph is larger than max_chars, force split it
            if p_len > self.max_chars:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                
                # Split giant paragraph by sentences or just arbitrarily
                # For simplicity, slice it by max_chars
                for i in range(0, p_len, self.max_chars):
                    chunks.append(p[i:i+self.max_chars].strip())
                continue
                
            if len(current_chunk) + p_len + 2 > self.max_chars:
                chunks.append(current_chunk.strip())
                current_chunk = p + "\n\n"
            else:
                current_chunk += p + "\n\n"
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        # Clean up any empty chunks
        return [c for c in chunks if c]

"""
file parse tool
support PDF、Markdown、TXT extract file of text
"""

import os
from pathlib import Path
from typing import List, Optional


def _read_text_with_fallback(file_path: str) -> str:
    """
    read text file，UTF-8 autodetect encoding on failed。
    
    Adopt a multi-level fallback strategy：
    1. Try it first UTF-8 Code Breaking
    2. use charset_normalizer Detection encoding
    3. fallback to chardet Detection encoding
    4. most final use UTF-8 + errors='replace' fallback
    
    Args:
        file_path: file path
        
    Returns:
        After decoding of text content
    """
    data = Path(file_path).read_bytes()
    
    # Try it first UTF-8
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        pass
    
    # Try use charset_normalizer Detection encoding
    encoding = None
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass
    
    # fallback to chardet
    if not encoding:
        try:
            import chardet
            result = chardet.detect(data)
            encoding = result.get('encoding') if result else None
        except Exception:
            pass
    
    # mostfinishedfallback：use UTF-8 + replace
    if not encoding:
        encoding = 'utf-8'
    
    return data.decode(encoding, errors='replace')


class FileParser:
    """file parse device"""
    
    SUPPORTED_EXTENSIONS = {'.pdf', '.md', '.markdown', '.txt'}
    
    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        """
        check file for support of format
        
        Args:
            file_path: file path
            
        Returns:
            return if file format is supported True
        """
        suffix = Path(file_path).suffix.lower()
        return suffix in cls.SUPPORTED_EXTENSIONS
    
    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        extract text from file
        
        Args:
            file_path: file path
            
        Returns:
            Extract of text content
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"file does not exist in: {file_path}")
        
        suffix = path.suffix.lower()
        
        if suffix not in cls.SUPPORTED_EXTENSIONS:
            raise ValueError(f"No support of file format: {suffix}")
        
        if suffix == '.pdf':
            return cls._extract_from_pdf(file_path)
        elif suffix in {'.md', '.markdown'}:
            return cls._extract_from_md(file_path)
        elif suffix == '.txt':
            return cls._extract_from_txt(file_path)
        
        raise ValueError(f"none method processing of file format: {suffix}")
    
    @staticmethod
    def _extract_from_pdf(file_path: str) -> str:
        """from PDF Extract text"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("need installation PyMuPDF: pip install PyMuPDF")
        
        text_parts = []
        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                if text.strip():
                    text_parts.append(text)
        
        return "\n\n".join(text_parts)
    
    @staticmethod
    def _extract_from_md(file_path: str) -> str:
        """from Markdown Extract text，support automatic encoding detection"""
        return _read_text_with_fallback(file_path)
    
    @staticmethod
    def _extract_from_txt(file_path: str) -> str:
        """from TXT Extract text，support automatic encoding detection"""
        return _read_text_with_fallback(file_path)
    
    @classmethod
    def extract_from_multiple(cls, file_paths: List[str]) -> str:
        """
        from multiple file extract text and merge
        
        Args:
            file_paths: file path list
            
        Returns:
            after merge of text
        """
        all_texts = []
        
        for i, file_path in enumerate(file_paths, 1):
            try:
                text = cls.extract_text(file_path)
                filename = Path(file_path).name
                all_texts.append(f"=== document {i}: {filename} ===\n{text}")
            except Exception as e:
                all_texts.append(f"=== document {i}: {file_path} (Extraction failed: {str(e)}) ===")
        
        return "\n\n".join(all_texts)


def split_text_into_chunks(
    text: str, 
    chunk_size: int = 500, 
    overlap: int = 50
) -> List[str]:
    """
    Split the text into small pieces
    
    Args:
        text: Original text
        chunk_size: of characters character count per block
        overlap: Overlapping characters count
        
    Returns:
        text block list
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        
        # Try splitting at the boundary of the in-sentence edge
        if end < len(text):
            # Find the nearest of sentences end
            for sep in ['。', '！', '？', '.\n', '!\n', '?\n', '\n\n', '. ', '! ', '? ']:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        
        # Next block from overlap position start
        start = end - overlap if end < len(text) else len(text)
    
    return chunks


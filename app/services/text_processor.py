"""
text processing service
"""

from typing import List, Optional
from ..utils.file_parser import FileParser, split_text_into_chunks


class TextProcessor:
    """text processor"""
    
    @staticmethod
    def extract_from_files(file_paths: List[str]) -> str:
        """extract text from multiple files"""
        return FileParser.extract_from_multiple(file_paths)
    
    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """
        split text
        
        Args:
            text: Original text
            chunk_size: Block size
            overlap: Overlap size
            
        Returns:
            text block list
        """
        return split_text_into_chunks(text, chunk_size, overlap)
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        Preprocessing text
        - remove extra whitespace
        - Standardized line switching
        
        Args:
            text: Original text
            
        Returns:
            Post-Processing of text
        """
        import re
        
        # Standardized line switching
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # remove continuous empty line（Keep most plus two swap lines）
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # remove line first line trailing blank
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
    @staticmethod
    def get_text_stats(text: str) -> dict:
        """get text statistics information"""
        return {
            "total_chars": len(text),
            "total_lines": text.count('\n') + 1,
            "total_words": len(text.split()),
        }


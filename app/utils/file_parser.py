"""
Universal file parsing tools for Veyronix.

Uploads are accepted for every extension. Text is extracted from:

- PDF (PyMuPDF, with pypdf fallback)
- Word / Excel / PowerPoint Open XML (docx, xlsx, pptx)
- OpenDocument (odt, ods, odp) and EPUB
- HTML, XML, RTF, Markdown, plain text, CSV/TSV, JSON/YAML and source code
- legacy binary Office files (best effort, printable-text scan)
- any other file that successfully decodes as text

Unknown binary files raise a clear error so one bad upload never crashes the
whole request; the API reports per-file failures instead.
"""

import logging
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import List
from xml.etree import ElementTree

logger = logging.getLogger(__name__)


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

    # Honour UTF-16/UTF-8 byte-order marks before guessing.
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16', errors='replace')
    if data.startswith(b'\xef\xbb\xbf'):
        return data.decode('utf-8-sig', errors='replace')

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


def _text_looks_readable(text: str) -> bool:
    """Return True when decoded text is mostly printable characters."""
    sample = (text or "")[:10000]
    if not sample.strip():
        return False

    bad = 0
    for char in sample:
        if char == '\ufffd':
            bad += 1
        elif ord(char) < 32 and char not in '\n\r\t':
            bad += 1

    return (bad / len(sample)) < 0.02


_PRINTABLE_RUN_RE = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f]{4,}")


def _extract_printable_runs(data: bytes) -> str:
    """Best-effort text recovery from an unknown binary payload."""
    best = ""
    for encoding in ("utf-16-le", "utf-8", "latin-1"):
        try:
            decoded = data.decode(encoding, errors="ignore")
        except (UnicodeDecodeError, LookupError):
            continue

        runs = [
            run.strip()
            for run in _PRINTABLE_RUN_RE.findall(decoded)
            if run.strip()
        ]
        text = "\n".join(runs)
        if len(text) > len(best):
            best = text

    return best


class _HTMLTextExtractor(HTMLParser):
    """Small HTML-to-text converter with script/style skipping."""

    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "main",
        "aside", "nav", "li", "tr", "td", "th", "h1", "h2", "h3", "h4",
        "h5", "h6", "blockquote", "pre", "ul", "ol", "table", "br",
    }
    SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class FileParser:
    """Universal file parser.

    ``is_supported`` intentionally returns True for every extension: the
    upload API accepts all files, and text extraction is decided by content.
    """

    TEXT_EXTENSIONS = {
        '.txt', '.text', '.md', '.markdown', '.log', '.rst', '.adoc',
        '.org', '.tex', '.csv', '.tsv', '.json', '.jsonl', '.ndjson',
        '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.properties',
        '.env', '.sql', '.py', '.js', '.jsx', '.mjs', '.cjs', '.ts',
        '.tsx', '.vue', '.svelte', '.java', '.kt', '.kts', '.scala',
        '.c', '.h', '.cpp', '.hpp', '.cc', '.cs', '.go', '.rb', '.php',
        '.rs', '.swift', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat',
        '.cmd', '.css', '.scss', '.less', '.srt', '.vtt', '.sub',
    }

    DOCUMENT_EXTENSIONS = {
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.xlsm', '.ppt',
        '.pptx', '.pptm', '.odt', '.ods', '.odp', '.epub', '.rtf',
        '.html', '.htm', '.xhtml', '.shtml', '.xml',
    }

    SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | DOCUMENT_EXTENSIONS

    @classmethod
    def is_supported(cls, file_path: str) -> bool:
        """Every extension is accepted; extraction may still fail on binary."""
        return True

    @classmethod
    def extract_text(cls, file_path: str) -> str:
        """
        Extract text from any file.

        Args:
            file_path: file path

        Returns:
            Extracted text content

        Raises:
            FileNotFoundError: when the file does not exist
            ValueError: when a binary file contains no readable text
            ImportError: when a required optional PDF backend is missing
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"file does not exist in: {file_path}")

        suffix = path.suffix.lower()

        if suffix == '.pdf':
            return cls._extract_from_pdf(file_path)
        if suffix == '.docx':
            return cls._extract_from_docx(file_path)
        if suffix in {'.xlsx', '.xlsm'}:
            return cls._extract_from_xlsx(file_path)
        if suffix in {'.pptx', '.pptm'}:
            return cls._extract_from_pptx(file_path)
        if suffix in {'.odt', '.ods', '.odp'}:
            return cls._extract_from_odf(file_path)
        if suffix == '.epub':
            return cls._extract_from_epub(file_path)
        if suffix in {'.html', '.htm', '.xhtml', '.shtml'}:
            return cls._extract_from_html(file_path)
        if suffix == '.rtf':
            return cls._extract_from_rtf(file_path)
        if suffix == '.xml':
            return cls._extract_from_xml(file_path)
        if suffix in {'.doc', '.xls', '.ppt'}:
            return cls._extract_legacy_office(file_path)
        if suffix in cls.TEXT_EXTENSIONS:
            text = _read_text_with_fallback(file_path)
            if _text_looks_readable(text):
                return text
            return cls._extract_from_any(file_path)

        # Unknown extension: accept it if the content is text-like.
        return cls._extract_from_any(file_path)

    # ------------------------------------------------------------------
    # Plain text
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_from_md(file_path: str) -> str:
        """from Markdown Extract text，support automatic encoding detection"""
        return _read_text_with_fallback(file_path)

    @staticmethod
    def _extract_from_txt(file_path: str) -> str:
        """from TXT Extract text，support automatic encoding detection"""
        return _read_text_with_fallback(file_path)

    @classmethod
    def _extract_from_any(cls, file_path: str) -> str:
        """Decode an unknown file as text; reject binary payloads cleanly."""
        data = Path(file_path).read_bytes()
        if not data:
            raise ValueError("File is empty")

        text = _read_text_with_fallback(file_path)
        if _text_looks_readable(text):
            return text

        raise ValueError(
            "No readable text could be extracted from this binary file. "
            "Upload a text-based document (PDF, DOCX, XLSX, PPTX, TXT, MD, "
            "CSV, HTML, XML, RTF, EPUB, etc.)."
        )

    # ------------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_from_pdf(file_path: str) -> str:
        """from PDF Extract text，PyMuPDF first and pypdf as fallback."""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            fitz = None

        if fitz is not None:
            try:
                text_parts = []
                with fitz.open(file_path) as doc:
                    for page in doc:
                        text = page.get_text()
                        if text.strip():
                            text_parts.append(text)
                if text_parts:
                    return "\n\n".join(text_parts)
            except Exception as exc:
                logger.warning("PyMuPDF failed for %s: %s", file_path, exc)

        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ImportError(
                "PDF extraction requires PyMuPDF or pypdf: "
                "pip install PyMuPDF pypdf"
            ) from exc

        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                text_parts.append(text)
        return "\n\n".join(text_parts)

    # ------------------------------------------------------------------
    # Office Open XML
    # ------------------------------------------------------------------

    @staticmethod
    def _local_tag(element) -> str:
        return element.tag.rsplit("}", 1)[-1]

    @classmethod
    def _extract_from_docx(cls, file_path: str) -> str:
        """Extract paragraphs from a DOCX archive without extra dependencies."""
        parts: List[str] = []
        try:
            with zipfile.ZipFile(file_path) as archive:
                names = [
                    name for name in archive.namelist()
                    if name.startswith("word/")
                    and name.endswith(".xml")
                    and (
                        name == "word/document.xml"
                        or "/header" in name
                        or "/footer" in name
                        or "footnotes" in name
                        or "endnotes" in name
                    )
                ]
                for name in sorted(names):
                    try:
                        root = ElementTree.fromstring(archive.read(name))
                    except (KeyError, ElementTree.ParseError):
                        continue
                    for element in root.iter():
                        if cls._local_tag(element) != "p":
                            continue
                        texts = [
                            child.text or ""
                            for child in element.iter()
                            if cls._local_tag(child) == "t"
                        ]
                        paragraph = "".join(texts).strip()
                        if paragraph:
                            parts.append(paragraph)
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid DOCX file") from exc

        return "\n".join(parts)

    @classmethod
    def _extract_from_pptx(cls, file_path: str) -> str:
        """Extract slide text from a PPTX archive."""
        parts: List[str] = []
        try:
            with zipfile.ZipFile(file_path) as archive:
                names = [
                    name for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ]
                names.sort(
                    key=lambda name: int(
                        re.search(r"(\d+)", Path(name).stem).group(1)
                    )
                )
                for name in names:
                    try:
                        root = ElementTree.fromstring(archive.read(name))
                    except (KeyError, ElementTree.ParseError):
                        continue
                    slide = [
                        element.text.strip()
                        for element in root.iter()
                        if cls._local_tag(element) == "t"
                        and (element.text or "").strip()
                    ]
                    if slide:
                        parts.append(" ".join(slide))
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid PPTX file") from exc

        return "\n\n".join(parts)

    @classmethod
    def _extract_from_xlsx(cls, file_path: str) -> str:
        """Extract cell values from an XLSX workbook."""
        try:
            from openpyxl import load_workbook
        except ImportError:
            return cls._extract_from_xlsx_xml(file_path)

        workbook = load_workbook(file_path, read_only=True, data_only=True)
        try:
            parts: List[str] = []
            for sheet in workbook.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    cells = [
                        str(value)
                        for value in row
                        if value is not None and str(value).strip()
                    ]
                    if cells:
                        parts.append("\t".join(cells))
            return "\n".join(parts)
        finally:
            workbook.close()

    @classmethod
    def _extract_from_xlsx_xml(cls, file_path: str) -> str:
        """Dependency-free XLSX fallback using the workbook XML."""
        try:
            with zipfile.ZipFile(file_path) as archive:
                shared: List[str] = []
                shared_name = "xl/sharedStrings.xml"
                if shared_name in archive.namelist():
                    try:
                        root = ElementTree.fromstring(
                            archive.read(shared_name)
                        )
                        for item in root.iter():
                            if cls._local_tag(item) != "si":
                                continue
                            shared.append("".join(
                                child.text or ""
                                for child in item.iter()
                                if cls._local_tag(child) == "t"
                            ))
                    except ElementTree.ParseError:
                        pass

                parts: List[str] = []
                sheets = sorted(
                    name for name in archive.namelist()
                    if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
                )
                for name in sheets:
                    try:
                        root = ElementTree.fromstring(archive.read(name))
                    except (KeyError, ElementTree.ParseError):
                        continue
                    for cell in root.iter():
                        if cls._local_tag(cell) != "c":
                            continue
                        value = ""
                        for child in cell.iter():
                            tag = cls._local_tag(child)
                            if tag in {"v", "t"} and child.text:
                                value = child.text
                        if cell.attrib.get("t") == "s":
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                        if value.strip():
                            parts.append(value)
                return "\n".join(parts)
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid XLSX file") from exc

    # ------------------------------------------------------------------
    # OpenDocument / EPUB / HTML / XML / RTF
    # ------------------------------------------------------------------

    @classmethod
    def _extract_from_odf(cls, file_path: str) -> str:
        """Extract text paragraphs from ODT/ODS/ODP content.xml."""
        try:
            with zipfile.ZipFile(file_path) as archive:
                root = ElementTree.fromstring(
                    archive.read("content.xml")
                )
        except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ValueError("Invalid OpenDocument file") from exc

        parts: List[str] = []
        for element in root.iter():
            if cls._local_tag(element) not in {"p", "h"}:
                continue
            text = "".join(element.itertext()).strip()
            if text:
                parts.append(text)
        return "\n".join(parts)

    @classmethod
    def _extract_from_epub(cls, file_path: str) -> str:
        """Extract readable XHTML/HTML chapters from an EPUB archive."""
        try:
            with zipfile.ZipFile(file_path) as archive:
                names = [
                    name for name in archive.namelist()
                    if name.lower().endswith((".xhtml", ".html", ".htm"))
                ]
                parts = []
                for name in sorted(names):
                    try:
                        raw = archive.read(name).decode(
                            "utf-8", errors="replace"
                        )
                    except KeyError:
                        continue
                    text = cls._html_to_text(raw)
                    if text:
                        parts.append(text)
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid EPUB file") from exc

        return "\n\n".join(parts)

    @staticmethod
    def _html_to_text(raw: str) -> str:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(raw)
            parser.close()
        except Exception:
            return re.sub(r"<[^>]+>", " ", raw)
        return parser.get_text()

    @classmethod
    def _extract_from_html(cls, file_path: str) -> str:
        raw = _read_text_with_fallback(file_path)
        return cls._html_to_text(raw)

    @classmethod
    def _extract_from_xml(cls, file_path: str) -> str:
        raw = _read_text_with_fallback(file_path)
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            return raw

        text = "\n".join(
            value.strip()
            for value in root.itertext()
            if value.strip()
        )
        return text or raw

    @classmethod
    def _extract_from_rtf(cls, file_path: str) -> str:
        raw = Path(file_path).read_bytes().decode(
            "latin-1", errors="replace"
        )

        # Decode escaped characters and unicode control words.
        raw = re.sub(
            r"\\'([0-9a-fA-F]{2})",
            lambda match: bytes([int(match.group(1), 16)]).decode(
                "cp1252", errors="replace"
            ),
            raw,
        )
        raw = re.sub(
            r"\\u(-?\d+)\??",
            lambda match: chr(int(match.group(1)) % 65536),
            raw,
        )

        raw = re.sub(r"\\par[d]?\b", "\n", raw)
        raw = re.sub(r"\\line\b", "\n", raw)
        raw = re.sub(r"\\tab\b", "\t", raw)
        raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
        raw = raw.replace("{", "").replace("}", "")
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()

    # ------------------------------------------------------------------
    # Legacy binary Office (best effort)
    # ------------------------------------------------------------------

    @classmethod
    def _extract_legacy_office(cls, file_path: str) -> str:
        """Best-effort text scan for .doc/.xls/.ppt binary containers."""
        data = Path(file_path).read_bytes()
        if not data:
            raise ValueError("File is empty")

        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                decoded = data.decode(encoding)
            except UnicodeDecodeError:
                continue
            if _text_looks_readable(decoded):
                return decoded

        runs = _extract_printable_runs(data)
        if _text_looks_readable(runs):
            return runs

        raise ValueError(
            "This legacy binary Office file could not be read. "
            "Save it as DOCX, XLSX or PPTX and upload it again."
        )

    # ------------------------------------------------------------------
    # Multiple files
    # ------------------------------------------------------------------

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
                all_texts.append(
                    f"=== document {i}: {file_path} "
                    f"(Extraction failed: {str(e)}) ==="
                )

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

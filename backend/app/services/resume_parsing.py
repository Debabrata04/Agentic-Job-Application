from pathlib import Path
from typing import Optional

import pymupdf
from docx import Document as DocxDocument

NEWLINE = chr(10)


def extract_text(file_path: str) -> Optional[str]:
    """Best-effort plain-text extraction from a resume file. Returns None if the
    format isn't supported or extraction fails - callers should treat a None result
    as 'no text available yet', not a hard error, since matching can simply skip
    resumes without extracted text rather than failing the whole upload."""
    suffix = Path(file_path).suffix.lower()

    try:
        if suffix == ".pdf":
            return _extract_pdf(file_path)
        if suffix == ".docx":
            return _extract_docx(file_path)
        if suffix == ".txt":
            return Path(file_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    return None


def _extract_pdf(file_path: str) -> str:
    """Uses PyMuPDF, not pypdf - pypdf concatenates adjacent text runs (e.g. a
    bold keyword next to regular text) with no space between them, corrupting
    real terms into nonsense tokens (seen on a real resume: "Milvus" became
    "DiskANNinMilvus", "LoRA" became "LoRAto"), which then made every skill
    NER extractor fail silently on that text. PyMuPDF's get_text() preserves
    the actual word boundaries, confirmed against that same resume."""
    doc = pymupdf.open(file_path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return NEWLINE.join(pages).strip()


def _extract_docx(file_path: str) -> str:
    doc = DocxDocument(file_path)
    paragraphs = [p.text for p in doc.paragraphs]
    return NEWLINE.join(paragraphs).strip()

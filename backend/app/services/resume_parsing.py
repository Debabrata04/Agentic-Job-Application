from pathlib import Path
from typing import Optional

from docx import Document as DocxDocument
from pypdf import PdfReader


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
    reader = PdfReader(file_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def _extract_docx(file_path: str) -> str:
    doc = DocxDocument(file_path)
    paragraphs = [p.text for p in doc.paragraphs]
    return "\n".join(paragraphs).strip()

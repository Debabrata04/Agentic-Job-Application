"""PDF-specific section-boundary detection using font metadata, not just text
shape. Verified against a real resume: every genuine section header (Skills,
Experience, Projects, and crucially also unrecognized ones like "Patent",
"Academic Achievements") shared the exact same font signature (CMCSC10 @
12pt), distinct from every body line in the document - a far more reliable
signal than the text-pattern heuristic in resume_sections.py, which can only
recognize headers whose wording happens to be in SECTION_HEADERS.

Requires PyMuPDF, which is also now used for the plain-text extraction in
resume_parsing.py (switched away from pypdf, which exposes no font metadata
at all). PDF only for now; DOCX would need a separate implementation via
python-docx's own run-level font/bold properties.
"""

from app.services.resume_sections import SECTION_HEADERS, _match_section_type


def _line_font_signature(line: dict) -> tuple | None:
    """Returns (font_name, size) if every span on this line shares one font -
    headers in testing were always font-uniform across the whole line, while
    body/bullet lines mixed multiple fonts (bullet glyph + bold keywords +
    regular text). A non-uniform line is never treated as a header boundary,
    whatever its individual spans' fonts are."""
    signatures = {(span["font"], round(span["size"], 1)) for span in line["spans"]}
    return signatures.pop() if len(signatures) == 1 else None


def _extract_lines_with_fonts(pdf_path: str) -> list[tuple[str, tuple | None]]:
    import pymupdf

    lines_out = []
    doc = pymupdf.open(pdf_path)
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if text:
                    lines_out.append((text, _line_font_signature(line)))
    doc.close()
    return lines_out


def get_header_boundary_lines(pdf_path: str) -> set[str] | None:
    """Returns the exact text of every line sharing the font signature used
    by this resume's own recognized headers (Skills/Experience/etc.) - this
    set then includes BOTH recognized headers AND unrecognized ones like
    "Patent" that happen to share the same header styling, which is exactly
    what lets resume_sections.py stop a section at "Patent" without needing
    to know that word in advance.

    Returns None if font signatures aren't available or aren't consistent
    across at least 2 recognized headers - not reliable enough to trust, so
    callers should fall back to text-pattern-only detection in that case.
    """
    try:
        lines = _extract_lines_with_fonts(pdf_path)
    except Exception:
        return None

    recognized_signatures = set()
    for text, signature in lines:
        if signature is not None and _match_section_type(text):
            recognized_signatures.add(signature)

    # Require the recognized headers to agree on ONE signature. If this
    # resume's headers aren't styled consistently, font-based detection
    # isn't trustworthy here - better to fall back than guess.
    if len(recognized_signatures) != 1:
        return None

    header_signature = recognized_signatures.pop()
    return {text for text, signature in lines if signature == header_signature}

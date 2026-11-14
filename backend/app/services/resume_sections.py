"""Regex header-matching to split resume text into Skills/Projects/Experience/
Education chunks, so skill extraction can run against just the sections that
actually matter - not the whole resume blob (summary/contact info add noise).
"""

import re

# Headers are matched case-insensitively against their own line (resumes
# almost universally put section headers on their own line, often in caps
# or title case, sometimes with a trailing colon). Order doesn't matter -
# each recognized header resets which section is "current", regardless of
# what came before or what sequence sections appear in (verified: Skills
# after Projects extracts identically to Skills before Projects).
SECTION_HEADERS: dict[str, list[str]] = {
    "skills": ["skills", "technical skills", "core skills", "key skills", "competencies"],
    "projects": ["projects", "personal projects", "academic projects", "key projects"],
    "experience": ["experience", "work experience", "professional experience", "employment history"],
    "education": ["education", "academic background", "qualifications"],
}

_HEADER_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z\s/&-]{1,40}?)\s*:?\s*$")


def _match_section_type(line: str) -> str | None:
    match = _HEADER_LINE_RE.match(line)
    if not match:
        return None
    candidate = match.group(1).strip().lower()
    for section_type, headers in SECTION_HEADERS.items():
        if candidate in headers:
            return section_type
    return None


def _looks_like_a_header(line: str) -> bool:
    """True for ANY short, title-case, standalone line - not just ones in our
    known vocabulary. Used to stop a section at an unrecognized header (e.g.
    "Patent", "Academic Achievements") instead of silently absorbing
    everything after it into whatever section was last recognized - that was
    a real bug, confirmed against a real resume: "Projects" swallowed Patent
    info, exam scores, and extracurricular content because none of those
    headers were in SECTION_HEADERS, so the scan never saw a reason to stop."""
    return _HEADER_LINE_RE.match(line) is not None


def split_into_sections(
    resume_text: str | None, extra_boundary_lines: set[str] | None = None
) -> dict[str, str]:
    """Returns {section_type: content} for whichever sections were found.
    A resume with no recognizable headers at all returns an empty dict -
    callers should treat that as "nothing to extract from", not an error.

    A section ends at the next header-looking line, whether or not that
    header is one we recognize - this is deliberate: better to lose a little
    content we can't confidently categorize than to misattribute it into
    the wrong section. Only known headers you want it must never be
    recognized - that's SECTION_HEADERS.

    extra_boundary_lines (optional): exact line texts that should also end
    a section even if they don't look like a header by text shape alone -
    e.g. from resume_pdf_structure's font-signature matching, which can
    catch boundaries (like "Patent") that happen to share header styling
    even when the text-pattern heuristic alone would miss them."""
    if not resume_text:
        return {}

    lines = resume_text.split("\n")
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    # When extra_boundary_lines is available (font-signature matching found
    # a reliable header font - see resume_pdf_structure), it's a strictly
    # more precise signal than the generic text-shape heuristic below, so
    # it's used INSTEAD of _looks_like_a_header, not in addition to it.
    # Verified on a real resume: with clean PyMuPDF-extracted text, a
    # job-title/company line like "AI Engineer" or "Valiance Solutions" is
    # itself a short, standalone, title-case line that _looks_like_a_header
    # would wrongly flag as an unrecognized section header, cutting
    # Experience off right after it starts - the font check doesn't make
    # that mistake, since those lines use a different font than the
    # resume's real headers.
    use_font_boundaries = extra_boundary_lines is not None
    boundary_lines = extra_boundary_lines or set()

    for line in lines:
        section_type = _match_section_type(line)
        if section_type:
            current_section = section_type
            sections.setdefault(current_section, [])
            continue
        is_boundary = (
            line.strip() in boundary_lines if use_font_boundaries else _looks_like_a_header(line)
        )
        if is_boundary:
            # An unrecognized header - stop accumulating into whatever
            # section was active, but don't start tracking this one.
            current_section = None
            continue
        if current_section:
            sections[current_section].append(line)

    return {section_type: "\n".join(content_lines).strip() for section_type, content_lines in sections.items() if "\n".join(content_lines).strip()}

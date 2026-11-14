"""BM25-style ranked keyword search over job postings, using Postgres's
built-in full-text search (tsvector/ts_rank) against JobPosting.search_vector
(a GENERATED ALWAYS column, see models.py - always in sync with title/
description, never written to directly).

Not literal BM25 - that needs the pg_search extension, which doesn't support
native Windows Postgres (see project history: ParadeDB only documents Docker
and macOS installs). ts_rank is Postgres's own built-in ranking function:
real, native, ranked full-text search with zero extra infrastructure, which
is what "scalable on AWS without sacrificing RDS" came down to.
"""

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

# tsquery syntax reserves & | ! ( ) : for boolean/weight operators - stripped
# from each keyword so nothing in resume/skill text can inject unintended
# query structure. What's left is plain words, safe to join with our own
# explicit & / | operators below.
_TSQUERY_RESERVED_RE = re.compile(r"[&|!():]")


def _build_or_tsquery(keywords: list[str]) -> str:
    """Turns ["python", "machine learning"] into "python | (machine & learning)" -
    each keyword's own words are AND'd together (so a multi-word skill still
    has to match as a phrase-ish unit), and different keywords are OR'd, so a
    posting matching ANY one keyword is retrieved - ranked by ts_rank, not
    filtered out for only matching one."""
    clauses = []
    for keyword in keywords:
        cleaned = _TSQUERY_RESERVED_RE.sub(" ", keyword).strip()
        words = [w for w in cleaned.split() if w]
        if not words:
            continue
        clauses.append("(" + " & ".join(words) + ")" if len(words) > 1 else words[0])
    return " | ".join(clauses)


def search_job_postings_by_keywords(db: Session, keywords: list[str], limit: int = 50) -> list[tuple[str, float]]:
    """Ranks job postings against a resume's deduplicated extracted_skills
    list. A posting matching even one keyword is retrieved; ts_rank orders
    by how many/how prominently keywords matched. The actual relevance call
    is made later by the cross-encoder (see matching.py) - this step's job
    is cheap ranked retrieval, not the final judgment.

    Returns [(job_posting_id, rank_score), ...] ordered by rank descending.
    """
    tsquery_string = _build_or_tsquery(keywords)
    if not tsquery_string:
        return []

    query = text("""
        SELECT id, ts_rank(search_vector, to_tsquery('english', :tsquery_string)) AS rank
        FROM job_postings
        WHERE search_vector @@ to_tsquery('english', :tsquery_string)
        ORDER BY rank DESC
        LIMIT :limit
    """)
    result = db.execute(query, {"tsquery_string": tsquery_string, "limit": limit})
    return [(str(row.id), float(row.rank)) for row in result]

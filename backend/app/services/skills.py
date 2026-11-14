"""Lightweight, local, rule-based skill/seniority extraction.

No external API calls (matches this project's local-model-only approach).
This is intentionally a keyword-vocabulary match, not NLP/NER - precision
over recall. Expect to keep extending SKILL_VOCAB as you see JDs that use
terms it misses; it's a flat list specifically so that's a one-line edit.
"""

import re

# Flat vocabulary of common tech/professional skill terms. Extend freely -
# matching is case-insensitive word-boundary matching, so multi-word terms
# (e.g. "machine learning") work too.
SKILL_VOCAB = [
    # languages
    "python", "java", "javascript", "typescript", "c++", "c#", "go", "golang", "rust",
    "ruby", "php", "swift", "kotlin", "scala", "r", "sql", "html", "css", "bash", "shell",
    # frameworks / libraries
    "react", "angular", "vue", "django", "flask", "fastapi", "spring", "spring boot",
    "express", "next.js", "node.js", "nodejs", ".net", "rails", "laravel",
    # data / ml
    "machine learning", "deep learning", "nlp", "computer vision", "pandas", "numpy",
    "pytorch", "tensorflow", "scikit-learn", "spark", "hadoop", "airflow", "etl",
    "data engineering", "data science", "llm", "generative ai",
    # databases
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch", "dynamodb",
    "cassandra", "sqlite", "oracle",
    # infra / devops
    "docker", "kubernetes", "aws", "azure", "gcp", "terraform", "ansible", "jenkins",
    "ci/cd", "github actions", "linux", "microservices", "graphql", "rest api", "grpc",
    "kafka", "rabbitmq",
    # practices
    "agile", "scrum", "tdd", "unit testing", "system design", "object-oriented design",
]

_SKILL_PATTERNS = [
    (skill, re.compile(r"(?<![a-z0-9])" + re.escape(skill.lower()) + r"(?![a-z0-9])"))
    for skill in SKILL_VOCAB
]

# Years-of-experience bands, ordered low -> high, each with the title words that
# typically signal it. Ranges overlap deliberately (e.g. 8-12 Lead/Staff and
# 10-15 Principal/Architect both cover 10-12) - real career ladders aren't a
# strict partition, and someone at year 11 can reasonably target either. When
# matching by years, band_from_years() returns the *highest* band whose floor
# the candidate has reached, since that's the most senior band they already
# qualify for - not the lowest or an arbitrary pick among overlapping bands.
SENIORITY_BANDS = [
    ("intern", 0, 0, ["intern", "graduate trainee", "fresher"]),
    ("entry", 0, 1, ["entry-level", "entry level", "graduate engineer", "junior associate"]),
    ("junior", 1, 2, ["junior developer", "junior engineer", "associate"]),
    ("mid", 2, 5, ["software engineer", "engineer ii", "analyst", "consultant"]),
    ("senior", 5, 8, ["senior engineer", "senior analyst", "senior developer", "senior"]),
    ("lead_staff", 8, 12, ["lead engineer", "staff engineer", "technical lead", "staff", "lead"]),
    ("principal_architect", 10, 15, ["principal engineer", "solution architect", "principal consultant", "principal", "architect"]),
    ("director_manager", 12, 18, ["director", "senior engineering manager", "engineering manager"]),
    ("vp_exec", 15, 20, ["vp of engineering", "head of engineering", "vp", "head of"]),
    ("csuite", 20, None, ["cto", "cio", "chief ai officer", "distinguished engineer", "chief"]),
]

# Back-compat name some callers may still reference; prefer SENIORITY_BANDS.
SENIORITY_LEVELS = [name for name, *_ in SENIORITY_BANDS]

# Word-boundary patterns per band, same approach as _SKILL_PATTERNS - a naive
# "word in text" substring check is unsafe here because short acronyms like
# "cto", "cio", "vp" are prone to false positives inside ordinary words (e.g.
# "director" contains the literal substring "cto": di-re-CTO-r).
_SENIORITY_PATTERNS = [
    (
        name,
        [re.compile(r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])") for word in title_words],
    )
    for name, _lo, _hi, title_words in SENIORITY_BANDS
]


def extract_skills(text: str | None) -> set[str]:
    if not text:
        return set()
    text_lower = text.lower()
    return {skill for skill, pattern in _SKILL_PATTERNS if pattern.search(text_lower)}


def detect_seniority(text: str | None) -> str | None:
    """Infer a band purely from title/body wording. Prefer band_from_years()
    when you have an actual years-of-experience number - text matching is a
    fallback for when you don't (e.g. a JD with no explicit years mentioned)."""
    if not text:
        return None
    text_lower = text.lower()
    # highest-ranked band wins on a tie (e.g. "Senior Staff Engineer" matches
    # both "senior" and "staff" - staff/lead ranks higher, so it wins).
    found = [name for name, patterns in _SENIORITY_PATTERNS if any(p.search(text_lower) for p in patterns)]
    return found[-1] if found else None


def band_from_years(years: float | None) -> str | None:
    """Maps a years-of-experience number to the highest band whose floor it
    has reached - e.g. 2 years -> 'mid' (2-5 band). With overlapping bands,
    11 years clears both lead_staff's floor (8) and principal_architect's
    floor (10); since SENIORITY_BANDS is ordered low -> high, taking the
    last match returns principal_architect, the more senior of the two."""
    if years is None:
        return None
    matching = [name for name, lo, _hi, _words in SENIORITY_BANDS if years >= lo]
    return matching[-1] if matching else None


def seniority_rank(level: str | None) -> int | None:
    if level is None:
        return None
    try:
        return SENIORITY_LEVELS.index(level)
    except ValueError:
        return None

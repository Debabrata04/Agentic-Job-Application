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

# Ordered low -> high. Used to compare JD seniority vs candidate's most recent title.
SENIORITY_LEVELS = [
    "intern", "junior", "entry", "associate", "mid-level", "senior",
    "staff", "principal", "lead", "director", "vp", "head of",
]


def extract_skills(text: str | None) -> set[str]:
    if not text:
        return set()
    text_lower = text.lower()
    return {skill for skill, pattern in _SKILL_PATTERNS if pattern.search(text_lower)}


def detect_seniority(text: str | None) -> str | None:
    if not text:
        return None
    text_lower = text.lower()
    # highest-ranked match wins (e.g. "Senior Staff Engineer" -> "staff")
    found = [level for level in SENIORITY_LEVELS if level in text_lower]
    return found[-1] if found else None


def seniority_rank(level: str | None) -> int | None:
    if level is None:
        return None
    try:
        return SENIORITY_LEVELS.index(level)
    except ValueError:
        return None

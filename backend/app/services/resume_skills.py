"""Tech-stack extraction from a resume's Skills/Projects/Experience sections.

Three extraction methods run in parallel and their results are unioned
(deduplicated, case-insensitive) rather than any one replacing another -
each catches cases the others miss, based on direct empirical testing:

1. Regex delimiter-split (extract_skills_from_list) - for the Skills section
   specifically, which is almost always a comma/bullet list. No vocabulary
   limit - whatever's listed becomes a skill, verbatim.

2. jjzha/jobbert_skill_extraction (NER, HuggingFace/transformers) - trained
   on SkillSpan (job-posting text). Good at picking skill mentions out of
   prose sentences; empirically found to extract NOTHING from a bare
   comma-list (never saw that format in training).

3. amjad-awad/skill-extractor (NER, spaCy) - trained on resumes AND job
   descriptions. In direct side-by-side testing against the same sentences,
   this one outperformed jjzha on both prose (3/4 skills vs 1 garbled span)
   and list format (6/6 skills vs 0/6) - but is kept alongside jjzha, not as
   a replacement, since combining independently-trained extractors catches
   more real skill mentions than any single one alone (e.g. jjzha and
   amjad-awad don't necessarily agree on every span even when both run).
"""

from functools import lru_cache

from app.services.resume_sections import split_into_sections

JJZHA_MODEL_NAME = "jjzha/jobbert_skill_extraction"
AMJAD_MODEL_REPO = "amjad-awad/skill-extractor"

# Sections every extractor (list-split and both NER models) draws from.
# "education" is excluded - degree/coursework text isn't a reliable skill
# signal the way Skills/Projects/Experience are.
SKILL_SOURCE_SECTIONS = ("skills", "projects", "experience")
NER_PROSE_SECTIONS = ("projects", "experience")  # list-split already covers "skills"

_MAX_SKILL_TOKEN_LENGTH = 40  # longer than this is a sentence fragment, not a single skill term

import re

# Stage 1: splits a line into (category label, item list) - first colon or
# " - " only (surrounded by spaces, so it doesn't match inside a hyphenated
# skill name like "Scikit-Learn" or "X-Ray"). Stage 2 below then splits just
# the item-list half on commas/etc - the category label itself (e.g.
# "Programming Languages") is discarded entirely, not kept as a pseudo-skill.
_CATEGORY_SEPARATOR_RE = re.compile(r":| - ")
_ITEM_DELIMITER_RE = re.compile(r"[,;|•·]|(?<!^)\s[-*]\s")
_LEADING_BULLET_RE = re.compile(r"^[\s•·*-]+")


def extract_skills_from_list(section_text: str) -> set[str]:
    """Two-stage, per-line extraction for a Skills-section-style list:
    (1) split each line into (category label, item list) on the first
    colon/dash separator, keeping only the item-list half; (2) split that
    half on commas/semicolons/bullets into individual skill tokens. No
    vocabulary limit - whatever's listed becomes a skill, verbatim."""
    if not section_text:
        return set()

    skills = set()
    for line in section_text.split("\n"):
        line = _LEADING_BULLET_RE.sub("", line).strip()
        if not line:
            continue

        parts = _CATEGORY_SEPARATOR_RE.split(line, maxsplit=1)
        item_text = parts[1] if len(parts) > 1 else parts[0]

        for token in _ITEM_DELIMITER_RE.split(item_text):
            cleaned = token.strip().strip("-*•· ").strip()
            if cleaned and len(cleaned) <= _MAX_SKILL_TOKEN_LENGTH:
                skills.add(cleaned.lower())
    return skills


@lru_cache(maxsize=1)
def _get_jjzha_pipeline():
    from transformers import pipeline

    return pipeline("token-classification", model=JJZHA_MODEL_NAME, aggregation_strategy=None)


@lru_cache(maxsize=1)
def _get_amjad_model():
    import spacy
    from huggingface_hub import snapshot_download

    model_path = snapshot_download(repo_id=AMJAD_MODEL_REPO)
    return spacy.load(model_path)


def _merge_bio_entities(raw_entities: list[dict]) -> list[str]:
    """jjzha's labels are bare 'B'/'I' (no '-SKILL' suffix), so HuggingFace's
    built-in aggregation can't tell what to merge - this does it manually.
    WordPiece subword pieces (prefixed '##') join directly onto the previous
    piece with no space; a real space only goes between whole-word tokens."""
    spans: list[str] = []
    current_words: list[str] = []

    def flush():
        if current_words:
            text = current_words[0]
            for piece in current_words[1:]:
                if piece.startswith("##"):
                    text += piece[2:]
                else:
                    text += " " + piece
            spans.append(text)

    for entity in raw_entities:
        label = entity.get("entity", "")
        word = entity.get("word", "").strip()
        if not word:
            continue
        if label == "B":
            flush()
            current_words = [word]
        elif label == "I" and current_words:
            current_words.append(word)
        else:
            flush()
            current_words = []

    flush()
    return spans


def _truncate_to_token_limit(tokenizer, text: str, max_tokens: int = 510) -> str:
    """Truncating by character count doesn't bound token count - 4000 chars
    of real prose tokenized to 935 tokens in testing, exceeding BERT's
    512-token position-embedding limit and crashing with a tensor-size
    mismatch. TokenClassificationPipeline has no truncation kwarg (confirmed
    via its _sanitize_parameters signature), so this truncates at the token
    level ourselves: encode with truncation, decode back to text, feed that
    into the pipeline. max_tokens=510 leaves room for the 2 special tokens
    ([CLS]/[SEP]) BERT adds on top."""
    token_ids = tokenizer.encode(text, truncation=True, max_length=max_tokens, add_special_tokens=False)
    return tokenizer.decode(token_ids)


def extract_skills_jjzha(text: str) -> set[str]:
    if not text:
        return set()
    ner = _get_jjzha_pipeline()
    truncated = _truncate_to_token_limit(ner.tokenizer, text)
    raw_entities = ner(truncated)
    return {span.lower() for span in _merge_bio_entities(raw_entities) if span}


def extract_skills_amjad(text: str) -> set[str]:
    if not text:
        return set()
    nlp = _get_amjad_model()
    doc = nlp(text[:4000])
    return {ent.text.strip().lower() for ent in doc.ents if ent.text.strip()}


def extract_tech_stack(
    resume_text: str | None, extra_boundary_lines: set[str] | None = None
) -> list[str]:
    """Runs all three extractors across the resume's relevant sections and
    returns the deduplicated union. Falls back to list-splitting the whole
    resume if no sections were detected at all (see resume_sections) -
    better to over-extract from noisier text than extract nothing because
    headers didn't match.

    extra_boundary_lines (optional): passed straight through to
    split_into_sections - for PDF resumes, resumes.py supplies the
    font-signature-matched boundary lines from resume_pdf_structure so
    sections stop at unrecognized headers too (e.g. "Patent")."""
    sections = split_into_sections(resume_text, extra_boundary_lines=extra_boundary_lines)
    if not sections:
        return sorted(extract_skills_from_list(resume_text or ""))

    skills: set[str] = set()

    if "skills" in sections:
        skills |= extract_skills_from_list(sections["skills"])

    prose_text = "\n".join(sections[s] for s in NER_PROSE_SECTIONS if s in sections)
    if prose_text.strip():
        skills |= extract_skills_jjzha(prose_text)

    full_source_text = "\n".join(sections[s] for s in SKILL_SOURCE_SECTIONS if s in sections)
    if full_source_text.strip():
        skills |= extract_skills_amjad(full_source_text)

    return sorted(skills)

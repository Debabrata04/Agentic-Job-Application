import html
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from app.models.models import (
    Application,
    ApplicationStatus,
    JobPosting,
    Resume,
    TargetRole,
    User,
    WorkExperience,
)
from app.services.resume_jd_scorer import aggregate_score_0_1, score_resume_jd_pairs
from app.services.skills import detect_seniority, extract_skills, seniority_rank

# Thresholds against the composite score (semantic relevance + skill overlap +
# seniority match, see _composite_score). Provisional starting points, not
# calibrated against real outcome data yet - expect to retune once there's
# enough real match/apply history to compare against.
HIGH_SIMILARITY_THRESHOLD = 0.55
LOW_SIMILARITY_THRESHOLD = 0.30

# How composite_score blends its signals. Any signal that can't be computed
# (e.g. no skills detected in a terse JD) is dropped and the rest re-normalized,
# rather than defaulting that signal to 0.
SCORE_WEIGHTS = {"relevance": 0.5, "skills": 0.35, "seniority": 0.15}

# How many years under the JD's stated requirement we still tolerate before
# treating it as a YOE mismatch worth flagging for review.
YOE_TOLERANCE_YEARS = 1.0

# Stage 1 (bi-encoder retrieval) narrows the whole discovered-postings pool
# down to this many shortlist candidates per user. Stage 2 (cross-encoder
# rerank, via resume_jd_scorer - a model fine-tuned specifically on resume<->JD
# pairs) only runs on the shortlist, where the extra accuracy is worth the
# extra compute - never on the full pool (could be thousands of postings).
SHORTLIST_SIZE = 20

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:-|to)?\s*\d*\+?\s*years?", re.IGNORECASE)


@lru_cache(maxsize=1)
def _get_bi_encoder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("all-MiniLM-L6-v2")


def clean_html(raw: Optional[str]) -> str:
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    return html.unescape(text).strip()


def extract_required_years(jd_text: str) -> Optional[float]:
    match = _YEARS_RE.search(jd_text)
    if not match:
        return None
    return float(match.group(1))


def embed_texts(texts: list[str]) -> np.ndarray:
    """Batch-embeds many texts in one call - this is the 'cheap' part of stage 1."""
    model = _get_bi_encoder()
    return model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)


@dataclass
class MatchResult:
    bucket: str  # "high", "medium", "low"
    similarity_score: float  # composite score (relevance + skills + seniority), see _composite_score
    selected_resume_id: Optional[str]
    matched_target_role_id: Optional[str]
    review_reason: Optional[str]
    matched_skills: list[str]
    missing_skills: list[str]


def _composite_score(
    relevance_score: float,
    candidate_skills: set[str],
    jd_skills: set[str],
    candidate_seniority_text: str,
    jd_seniority_text: str,
) -> tuple[float, Optional[float], Optional[float]]:
    """Blend the stage-2 relevance score with skill overlap and seniority match.

    Any signal that can't be computed is dropped and the remaining weights
    re-normalized, rather than penalizing the score for missing data.
    Returns (composite_score, skill_score_or_None, seniority_score_or_None).
    """
    skill_score = len(candidate_skills & jd_skills) / len(jd_skills) if jd_skills else None

    candidate_level = detect_seniority(candidate_seniority_text)
    jd_level = detect_seniority(jd_seniority_text)
    seniority_score = None
    if candidate_level is not None and jd_level is not None:
        candidate_rank = seniority_rank(candidate_level)
        jd_rank = seniority_rank(jd_level)
        rank_gap = jd_rank - candidate_rank  # positive = JD wants more seniority than candidate has
        seniority_score = 1.0 if rank_gap <= 0 else max(0.0, 1.0 - 0.3 * rank_gap)

    components = [("relevance", relevance_score)]
    if skill_score is not None:
        components.append(("skills", skill_score))
    if seniority_score is not None:
        components.append(("seniority", seniority_score))

    total_weight = sum(SCORE_WEIGHTS[name] for name, _ in components)
    composite = sum(SCORE_WEIGHTS[name] * value for name, value in components) / total_weight
    return composite, skill_score, seniority_score


def _finalize_match(
    db: Session, job_posting: JobPosting, user: User, best_resume: Resume, jd_text: str, relevance_score: float
) -> MatchResult:
    """Combines a stage-2 relevance score with skills/seniority/YOE signals into a bucketed MatchResult."""
    matched_target_role = (
        db.query(TargetRole).filter(TargetRole.preferred_resume_id == best_resume.id).first()
    )

    # Candidate skills: whatever the resume text mentions, plus every keyword
    # you've explicitly entered across your target roles (those are hand-picked
    # must-haves, so they're at least as trustworthy as resume text matches).
    target_role_keywords = {
        kw.lower()
        for role in db.query(TargetRole).filter(TargetRole.user_id == user.id).all()
        for kw in (role.keywords or [])
    }
    candidate_skills = extract_skills(best_resume.parsed_text) | target_role_keywords
    jd_skills = extract_skills(jd_text)
    matched_skills = sorted(candidate_skills & jd_skills)
    missing_skills = sorted(jd_skills - candidate_skills)

    latest_titles = " ".join(
        title for (title,) in db.query(WorkExperience.title).filter(WorkExperience.user_id == user.id).all()
    )
    candidate_seniority_text = f"{latest_titles} {best_resume.parsed_text or ''}"
    jd_seniority_text = f"{job_posting.title or ''} {jd_text}"

    composite_score, skill_score, seniority_score = _composite_score(
        relevance_score, candidate_skills, jd_skills, candidate_seniority_text, jd_seniority_text
    )

    required_years = extract_required_years(jd_text)
    yoe_ok = True
    yoe_note = None
    if required_years is not None and user.years_of_experience is not None:
        if user.years_of_experience < required_years - YOE_TOLERANCE_YEARS:
            yoe_ok = False
            yoe_note = (
                f"JD asks for ~{required_years:.0f}+ years; "
                f"your profile has {user.years_of_experience:.1f}"
            )

    if composite_score < LOW_SIMILARITY_THRESHOLD:
        return MatchResult(
            "low", composite_score, str(best_resume.id), None, "Match score too low", matched_skills, missing_skills
        )

    if composite_score >= HIGH_SIMILARITY_THRESHOLD and yoe_ok:
        return MatchResult(
            "high",
            composite_score,
            str(best_resume.id),
            str(matched_target_role.id) if matched_target_role else None,
            None,
            matched_skills,
            missing_skills,
        )

    reasons = []
    if composite_score < HIGH_SIMILARITY_THRESHOLD:
        reasons.append(f"Match score {composite_score:.2f} below high-confidence threshold")
    if missing_skills:
        reasons.append(f"Missing skills: {', '.join(missing_skills)}")
    if not matched_target_role:
        reasons.append("Best-matching resume isn't linked to any of your target roles")
    if not yoe_ok:
        reasons.append(yoe_note)

    return MatchResult(
        "medium",
        composite_score,
        str(best_resume.id),
        str(matched_target_role.id) if matched_target_role else None,
        "; ".join(reasons),
        matched_skills,
        missing_skills,
    )


def _create_application(db: Session, user: User, job_posting: JobPosting, result: MatchResult) -> Application:
    status = ApplicationStatus.AUTO_APPLIED if result.bucket == "high" else ApplicationStatus.PENDING_REVIEW
    application = Application(
        user_id=user.id,
        job_posting_id=job_posting.id,
        resume_id=result.selected_resume_id,
        target_role_id=result.matched_target_role_id,
        similarity_score=result.similarity_score,
        status=status,
        review_reason=result.review_reason,
        matched_skills=result.matched_skills,
        missing_skills=result.missing_skills,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def match_all_unmatched_jobs_for_user(db: Session, user: User) -> list[Application]:
    """Two-stage matching: cheap bi-encoder retrieval across the whole unmatched
    pool narrows it to a shortlist, then the slower but more accurate
    cross-encoder reranks just that shortlist before final scoring.
    """
    already_matched_job_ids = {
        row.job_posting_id for row in db.query(Application.job_posting_id).filter(Application.user_id == user.id).all()
    }
    query = db.query(JobPosting)
    if already_matched_job_ids:
        query = query.filter(JobPosting.id.notin_(already_matched_job_ids))

    candidates = []  # (job_posting, jd_text)
    for job_posting in query.all():
        jd_text = clean_html(job_posting.description)
        if jd_text:
            candidates.append((job_posting, jd_text))
    if not candidates:
        return []

    resumes = (
        db.query(Resume)
        .filter(Resume.user_id == user.id, Resume.is_active.is_(True), Resume.parsed_text.isnot(None))
        .all()
    )
    if not resumes:
        return []

    # --- Stage 1: bi-encoder retrieval, cheap, run once across everything ---
    resume_embeddings = embed_texts([r.parsed_text for r in resumes])
    jd_embeddings = embed_texts([jd_text for _, jd_text in candidates])

    # similarity matrix: (num_jobs, num_resumes); best resume per job = argmax along axis 1
    sim_matrix = jd_embeddings @ resume_embeddings.T
    best_resume_idx = sim_matrix.argmax(axis=1)
    best_embedding_scores = sim_matrix[np.arange(len(candidates)), best_resume_idx]

    ranked = sorted(
        zip(candidates, best_resume_idx, best_embedding_scores),
        key=lambda row: -row[2],
    )
    shortlist = [
        (job_posting, jd_text, resumes[resume_idx], float(score))
        for (job_posting, jd_text), resume_idx, score in ranked
        if score >= LOW_SIMILARITY_THRESHOLD
    ][:SHORTLIST_SIZE]
    if not shortlist:
        return []

    # --- Stage 2: cross-encoder rerank, only on the shortlist ---
    pairs = [(best_resume.parsed_text, jd_text) for _, jd_text, best_resume, _ in shortlist]
    aspect_scores = score_resume_jd_pairs(pairs)
    relevance_scores = [aggregate_score_0_1(scores) for scores in aspect_scores]

    results = []
    for (job_posting, jd_text, best_resume, _embedding_score), relevance_score in zip(shortlist, relevance_scores):
        result = _finalize_match(db, job_posting, user, best_resume, jd_text, float(relevance_score))
        if result.bucket == "low":
            continue
        results.append(_create_application(db, user, job_posting, result))
    return results

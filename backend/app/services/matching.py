import html
import re
from dataclasses import dataclass
from typing import Optional

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
from app.services.job_search import search_job_postings_by_keywords
from app.services.resume_jd_scorer import aggregate_score_0_1, score_resume_jd_pairs
from app.services.skills import band_from_years, detect_seniority, extract_skills, seniority_rank

# "Highly relevant" is decided by resume_jd_scorer's own aggregate score, not
# the composite below. The model card (kasraahmadi/job-eval-modernbert)
# documents no numeric threshold - only unlabeled qualitative bands (Strong
# match / Promising fit / Stretch fit / Low alignment) over its 1-5 scale.
# 0.75 on the rescaled 0-1 scale is our own interpretation of "Strong match"
# as the top quartile (4.0-5.0 before rescaling) - not a published default.
HIGH_RELEVANCE_THRESHOLD = 0.75

# Below this composite score (relevance + skills + seniority blend, see
# _composite_score), a job is discarded outright - no Application row at all.
# Provisional, not calibrated against real outcome data yet.
LOW_SIMILARITY_THRESHOLD = 0.30

# How composite_score blends its signals. Any signal that can't be computed
# (e.g. no skills detected in a terse JD) is dropped and the rest re-normalized,
# rather than defaulting that signal to 0.
SCORE_WEIGHTS = {"relevance": 0.5, "skills": 0.35, "seniority": 0.15}

# How many years under the JD's stated requirement we still tolerate before
# treating it as a YOE mismatch worth flagging for review.
YOE_TOLERANCE_YEARS = 1.0

# How many top-ranked (by ts_rank) postings to retrieve per resume from the
# BM25-style keyword search before the seniority filter and cross-encoder
# run. Same role SHORTLIST_SIZE played in the earlier bi-encoder-based
# design - caps how many postings reach the expensive cross-encoder stage.
BM25_SHORTLIST_SIZE = 50

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:-|to)?\s*\d*\+?\s*years?", re.IGNORECASE)


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
    candidate_level: Optional[str],
    jd_level: Optional[str],
) -> tuple[float, Optional[float], Optional[float]]:
    """Blend the stage-2 relevance score with skill overlap and seniority match.

    Any signal that can't be computed is dropped and the remaining weights
    re-normalized, rather than penalizing the score for missing data.
    candidate_level/jd_level are seniority band names (see skills.band_from_years
    and skills.detect_seniority) - resolved by the caller, since choosing between
    a YOE-number-based band and a text-based fallback needs context this
    function doesn't have.
    Returns (composite_score, skill_score_or_None, seniority_score_or_None).
    """
    skill_score = len(candidate_skills & jd_skills) / len(jd_skills) if jd_skills else None

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


def _resume_seniority_level(db: Session, user: User, resume: Resume) -> Optional[str]:
    """A real YOE number (user-level, shared across all of a user's resumes)
    always wins over text inference - it's unambiguous, whereas title wording
    varies. Falls back to detecting seniority from this resume's own text
    (plus the user's logged work-experience titles) only when no YOE is set."""
    level = band_from_years(user.years_of_experience)
    if level is not None:
        return level
    latest_titles = " ".join(
        title for (title,) in db.query(WorkExperience.title).filter(WorkExperience.user_id == user.id).all()
    )
    return detect_seniority(f"{latest_titles} {resume.parsed_text or ''}")


def _jd_seniority_level(job_posting: JobPosting, jd_text: str, required_years: Optional[float]) -> Optional[str]:
    if required_years is not None:
        level = band_from_years(required_years)
        if level is not None:
            return level
    return detect_seniority(f"{job_posting.title or ''} {jd_text}")


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

    required_years = extract_required_years(jd_text)
    candidate_level = _resume_seniority_level(db, user, best_resume)
    jd_level = _jd_seniority_level(job_posting, jd_text, required_years)

    composite_score, skill_score, seniority_score = _composite_score(
        relevance_score, candidate_skills, jd_skills, candidate_level, jd_level
    )

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

    if relevance_score >= HIGH_RELEVANCE_THRESHOLD and yoe_ok:
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
    if relevance_score < HIGH_RELEVANCE_THRESHOLD:
        reasons.append(f"Relevance score {relevance_score:.2f} below high-confidence threshold ({HIGH_RELEVANCE_THRESHOLD})")
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
    """Per-resume BM25 retrieval (search_job_postings_by_keywords, using each
    resume's stored extracted_skills - computed once at upload, reused here
    every time, never recomputed per-match) narrows the whole discovered-
    postings pool to a ranked shortlist - no model calls for this step. Every
    job shortlisted by any resume then goes through the cross-encoder
    (resume_jd_scorer, fine-tuned specifically on resume<->JD pairs); there is
    no hard seniority pre-filter any more - seniority only affects the final
    composite score gradually (_composite_score inside _finalize_match). A
    job that's a candidate for more than one resume is scored against each
    and only the higher-scoring resume is kept, so every job gets at most one
    Application row.
    """
    # str(...) here matters: search_job_postings_by_keywords below returns str
    # ids, while row.job_posting_id from the ORM is a uuid.UUID - without this,
    # the set subtraction at job_ids below silently never removes anything
    # (UUID("x") != "x"), so already-applied jobs were never actually excluded.
    already_matched_job_ids = {
        str(row.job_posting_id)
        for row in db.query(Application.job_posting_id).filter(Application.user_id == user.id).all()
    }

    resumes = (
        db.query(Resume)
        .filter(Resume.user_id == user.id, Resume.is_active.is_(True), Resume.parsed_text.isnot(None))
        .all()
    )
    if not resumes:
        return []

    shortlisted_job_ids_by_resume: dict = {}
    all_shortlisted_job_ids: set = set()
    for resume in resumes:
        job_ids_and_ranks = search_job_postings_by_keywords(
            db, resume.extracted_skills or [], limit=BM25_SHORTLIST_SIZE
        )
        job_ids = {jid for jid, _rank in job_ids_and_ranks} - already_matched_job_ids
        shortlisted_job_ids_by_resume[resume.id] = job_ids
        all_shortlisted_job_ids |= job_ids

    if not all_shortlisted_job_ids:
        return []

    shortlisted_postings = db.query(JobPosting).filter(JobPosting.id.in_(all_shortlisted_job_ids)).all()

    jobs = []
    jd_text_by_job_id = {}
    for job_posting in shortlisted_postings:
        jd_text = clean_html(job_posting.description)
        if not jd_text:
            continue
        jobs.append(job_posting)
        jd_text_by_job_id[job_posting.id] = jd_text
    if not jobs:
        return []

    candidates_by_job: dict = {}
    for job_posting in jobs:
        # Seniority is no longer a hard pre-filter here - every resume that
        # BM25 shortlisted for this job is a candidate. Seniority still
        # factors into the final score via _composite_score/_finalize_match
        # below, just gradually rather than as a reject/pass gate.
        # job_posting.id is a uuid.UUID from the ORM; shortlisted_job_ids_by_resume
        # holds str ids (from search_job_postings_by_keywords) - compare as str on
        # both sides, since UUID("x") == "x" is always False in Python.
        passing_resumes = [r for r in resumes if str(job_posting.id) in shortlisted_job_ids_by_resume[r.id]]
        if passing_resumes:
            candidates_by_job[job_posting.id] = passing_resumes

    if not candidates_by_job:
        return []

    results = []
    for job_posting in jobs:
        resume_options = candidates_by_job.get(job_posting.id)
        if not resume_options:
            continue
        jd_text = jd_text_by_job_id[job_posting.id]

        pairs = [(r.parsed_text, jd_text) for r in resume_options]
        aspect_scores_list = score_resume_jd_pairs(pairs)
        relevance_scores = [aggregate_score_0_1(scores) for scores in aspect_scores_list]

        best_idx = max(range(len(relevance_scores)), key=lambda i: relevance_scores[i])
        best_resume = resume_options[best_idx]
        best_relevance_score = relevance_scores[best_idx]

        result = _finalize_match(db, job_posting, user, best_resume, jd_text, best_relevance_score)
        if result.bucket == "low":
            continue
        results.append(_create_application(db, user, job_posting, result))
    return results

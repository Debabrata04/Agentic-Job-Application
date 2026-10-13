import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import JobPosting, JobSource
from app.schemas.schemas import JobPostingOut
from app.services.job_sources.smartrecruiters import fetch_job_description

router = APIRouter(prefix="/job-postings", tags=["job-postings"])


@router.get("", response_model=list[JobPostingOut])
def list_job_postings(
    company: str | None = Query(default=None),
    source: str | None = Query(default=None),
    title: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(JobPosting)
    if company:
        query = query.filter(JobPosting.company.ilike(f"%{company}%"))
    if source:
        query = query.filter(JobPosting.source == source)
    if title:
        query = query.filter(JobPosting.title.ilike(f"%{title}%"))
    return query.order_by(JobPosting.discovered_at.desc()).offset(offset).limit(limit).all()


@router.get("/count")
def count_job_postings(
    company: str | None = Query(default=None),
    source: str | None = Query(default=None),
    title: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(JobPosting)
    if company:
        query = query.filter(JobPosting.company.ilike(f"%{company}%"))
    if source:
        query = query.filter(JobPosting.source == source)
    if title:
        query = query.filter(JobPosting.title.ilike(f"%{title}%"))
    return {"count": query.count()}


@router.get("/{job_posting_id}", response_model=JobPostingOut)
def get_job_posting(job_posting_id: uuid.UUID, db: Session = Depends(get_db)):
    posting = db.get(JobPosting, job_posting_id)
    if not posting:
        raise HTTPException(status_code=404, detail="Job posting not found")
    return posting


@router.post("/{job_posting_id}/fetch-description", response_model=JobPostingOut)
def fetch_description(job_posting_id: uuid.UUID, db: Session = Depends(get_db)):
    """Lazily fetch and cache the full description for postings whose source doesn't
    include it in the bulk discovery listing (currently: smartrecruiters)."""
    posting = db.get(JobPosting, job_posting_id)
    if not posting:
        raise HTTPException(status_code=404, detail="Job posting not found")

    if posting.description:
        return posting

    if posting.source != "smartrecruiters":
        raise HTTPException(status_code=400, detail="This source already includes full descriptions")

    job_source = (
        db.query(JobSource)
        .filter(JobSource.platform == "smartrecruiters", JobSource.company_name == posting.company)
        .first()
    )
    if not job_source:
        raise HTTPException(status_code=404, detail="No matching job source found to resolve the slug")

    description = fetch_job_description(job_source.slug, posting.external_id)
    if description:
        posting.description = description
        db.commit()
        db.refresh(posting)
    return posting

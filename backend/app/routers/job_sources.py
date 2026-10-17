import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import JobSource
from app.schemas.schemas import JobSourceCatalogOut, JobSourceIn, JobSourceOut, JobSourceUpdate
from app.services.job_discovery import discover_all_active, discover_for_source
from app.services.job_sources.catalog import list_catalog

router = APIRouter(prefix="/job-sources", tags=["job-sources"])


@router.get("/catalog", response_model=list[JobSourceCatalogOut])
def get_catalog(db: Session = Depends(get_db)):
    """Known companies with their platform+slug already resolved, for a selection dropdown.
    Excludes companies already being tracked."""
    tracked = {(s.platform, s.slug) for s in db.query(JobSource.platform, JobSource.slug).all()}
    return [entry for entry in list_catalog() if (entry.platform, entry.slug) not in tracked]


@router.post("", response_model=JobSourceOut, status_code=201)
def create_job_source(payload: JobSourceIn, db: Session = Depends(get_db)):
    if payload.platform not in {"greenhouse", "lever", "smartrecruiters"}:
        raise HTTPException(status_code=400, detail="platform must be one of: greenhouse, lever, smartrecruiters")

    source = JobSource(**payload.model_dump())
    db.add(source)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This platform+slug is already tracked")
    db.refresh(source)
    return source


@router.get("", response_model=list[JobSourceOut])
def list_job_sources(db: Session = Depends(get_db)):
    return db.query(JobSource).all()


@router.patch("/{source_id}", response_model=JobSourceOut)
def update_job_source(source_id: uuid.UUID, payload: JobSourceUpdate, db: Session = Depends(get_db)):
    source = db.get(JobSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Job source not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=204)
def delete_job_source(source_id: uuid.UUID, db: Session = Depends(get_db)):
    source = db.get(JobSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Job source not found")
    db.delete(source)
    db.commit()


@router.post("/{source_id}/discover")
def discover_one(source_id: uuid.UUID, db: Session = Depends(get_db)):
    source = db.get(JobSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Job source not found")
    return discover_for_source(db, source)


@router.post("/discover-all")
def discover_all(db: Session = Depends(get_db)):
    return discover_all_active(db)

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.models import JobPosting, JobSource
from app.services.job_sources import fetch_jobs_for_source


def discover_for_source(db: Session, job_source: JobSource) -> dict:
    try:
        normalized_jobs = fetch_jobs_for_source(job_source.platform, job_source.slug, job_source.company_name)
    except Exception as exc:
        job_source.last_polled_at = datetime.utcnow()
        job_source.last_poll_status = "error"
        job_source.last_poll_error = str(exc)
        db.commit()
        return {"company": job_source.company_name, "platform": job_source.platform, "error": str(exc)}

    existing_external_ids = {
        row.external_id
        for row in db.query(JobPosting.external_id).filter(JobPosting.source == job_source.platform).all()
    }

    new_count = 0
    for job in normalized_jobs:
        if job.external_id in existing_external_ids:
            continue
        db.add(
            JobPosting(
                source=job.source,
                external_id=job.external_id,
                company=job.company,
                title=job.title,
                description=job.description,
                location=job.location,
                url=job.url,
            )
        )
        existing_external_ids.add(job.external_id)
        new_count += 1

    job_source.last_polled_at = datetime.utcnow()
    job_source.last_poll_status = "ok"
    job_source.last_poll_error = None
    db.commit()

    return {
        "company": job_source.company_name,
        "platform": job_source.platform,
        "fetched": len(normalized_jobs),
        "new": new_count,
    }


def discover_all_active(db: Session) -> list[dict]:
    sources = db.query(JobSource).filter(JobSource.is_active.is_(True)).all()
    return [discover_for_source(db, source) for source in sources]

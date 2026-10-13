import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.models.models import Application, ApplicationStatus, JobPosting, User
from app.schemas.schemas import ApplicationMarkSubmitted, ApplicationOut
from app.services.application_filler import open_application_live, prefill_application
from app.services.email_notifier import send_email
from app.services.matching import match_all_unmatched_jobs_for_user
from app.services.profile_completeness import get_missing_profile_items

router = APIRouter(tags=["matching"])


def _store_prefill_result_and_notify(db: Session, application: Application, user: User, result: dict) -> None:
    application.filled_answers = result["filled_answers"]
    application.missing_fields = result["missing_fields"]
    application.optional_missing_fields = result.get("optional_missing_fields") or []
    application.screenshot_path = result["screenshot_path"]
    application.prefilled_at = datetime.utcnow()
    application.status = (
        ApplicationStatus.NEEDS_MORE_INFO if result["missing_fields"] else ApplicationStatus.PENDING_REVIEW
    )
    db.commit()
    db.refresh(application)

    job_posting = db.get(JobPosting, application.job_posting_id)
    job_label = f"{job_posting.title} at {job_posting.company}" if job_posting else "a job"
    raw_apply_url = result.get("apply_url") or (job_posting.url if job_posting else None)
    # #jobagent_app_id=... is a URL fragment - never sent to the server, purely
    # client-side - so it rides along with the link for the bookmarklet (see
    # frontend/bookmarklet.js) to read, without changing where the link goes.
    apply_url = f"{raw_apply_url}#jobagent_app_id={application.id}" if raw_apply_url else None
    apply_line = (
        f"\n\nApply here: {apply_url}"
        "\n(Once there, click your \"Autofill this application\" bookmarklet to fill it in your own browser.)"
        if apply_url else ""
    )

    # The apply link always opens a fresh, blank form - it's the public job
    # posting URL, not a live session, so nothing typed into our (separate,
    # already-closed) headless browser carries over to it. These values are
    # what to copy in yourself; verify each one against the screenshot before
    # you do, since form frameworks can silently drop a fill (see field notes
    # in application_filler.py).
    filled_answers = result.get("filled_answers") or {}
    if filled_answers:
        filled_block = (
            "\n\nWe filled these fields in our own preview browser (copy them into the real form - "
            "the link below always opens blank, double-check against the attached screenshot first):\n\n"
            + "\n".join(f"- {label}: {value}" for label, value in filled_answers.items())
        )
    else:
        filled_block = "\n\nWe weren't able to confidently fill in any fields on this form."

    if result["missing_fields"]:
        send_email(
            db, user.id, user.email,
            subject=f"Action needed: missing info for your application to {job_label}",
            body=(
                f"We pre-filled what we could for {job_label}, but these REQUIRED fields had no data in your profile:\n\n"
                + "\n".join(f"- {f}" for f in result["missing_fields"])
                + "\n\nAdd this info to your profile, then review and submit the application in your dashboard."
                + filled_block
                + apply_line
            ),
            application_id=application.id,
        )

    optional_missing = result.get("optional_missing_fields") or []
    body = (
        f"We pre-filled an application for {job_label} and stopped before submitting.\n"
        "Review the filled answers and screenshot in your dashboard, then submit it yourself and mark it as submitted."
        + filled_block
        + apply_line
    )
    if optional_missing:
        body += (
            "\n\nThese optional fields were also left blank (no data in your profile):\n\n"
            + "\n".join(f"- {f}" for f in optional_missing)
        )
    send_email(
        db, user.id, user.email,
        subject=f"Application pre-filled: {job_label}",
        body=body,
        application_id=application.id,
    )


@router.post("/users/{user_id}/match-jobs", response_model=list[ApplicationOut])
def match_jobs(user_id: uuid.UUID, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return match_all_unmatched_jobs_for_user(db, user)


@router.get("/users/{user_id}/applications", response_model=list[ApplicationOut])
def list_applications(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Application).filter(Application.user_id == user_id).all()


@router.post("/users/{user_id}/applications/{application_id}/prefill", response_model=ApplicationOut)
def prefill_one(user_id: uuid.UUID, application_id: uuid.UUID, db: Session = Depends(get_db)):
    application = db.query(Application).filter(Application.id == application_id, Application.user_id == user_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    user = db.get(User, user_id)
    try:
        result = prefill_application(db, application)
    except Exception:
        import traceback
        application.status = ApplicationStatus.FAILED
        application.review_reason = f"Pre-fill failed: {traceback.format_exc()}"
        db.commit()
        raise HTTPException(status_code=500, detail=application.review_reason)
    _store_prefill_result_and_notify(db, application, user, result)
    return application


def _retry_failed_applications(user_id: uuid.UUID) -> None:
    """Runs in the background (FastAPI BackgroundTasks) - retrying all of a
    user's FAILED applications can take hours (one Playwright run per job), so
    this can't block the HTTP request that triggers it. Uses its own session
    since the request-scoped one is closed by the time this runs."""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user:
            return
        failed_applications = (
            db.query(Application)
            .filter(Application.user_id == user_id, Application.status == ApplicationStatus.FAILED)
            .all()
        )
        for application in failed_applications:
            try:
                result = prefill_application(db, application)
            except Exception:
                import traceback
                application.status = ApplicationStatus.FAILED
                application.review_reason = f"Pre-fill failed: {traceback.format_exc()}"
                db.commit()
                continue
            _store_prefill_result_and_notify(db, application, user, result)
    finally:
        db.close()


@router.post("/users/{user_id}/applications/retry-failed")
def retry_failed_applications(user_id: uuid.UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    count = (
        db.query(Application)
        .filter(Application.user_id == user_id, Application.status == ApplicationStatus.FAILED)
        .count()
    )
    background_tasks.add_task(_retry_failed_applications, user_id)
    return {"queued": count}


@router.get("/applications/{application_id}/filled-answers")
def get_filled_answers(application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Used by the browser extension (extension/) to autofill the real
    application page in the user's own browser from its popup context - the
    email link alone can never do this, since the Playwright fill happens in a
    separate, already-closed server-side browser session. Keyed by
    application_id alone (not nested under /users/{id}/) since that's all the
    extension has on hand (reads it from the page URL)."""
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return {
        "filled_answers": application.filled_answers or {},
        "missing_fields": application.missing_fields or [],
        "optional_missing_fields": application.optional_missing_fields or [],
    }


@router.post("/users/{user_id}/applications/{application_id}/open-live")
def open_application_live_endpoint(user_id: uuid.UUID, application_id: uuid.UUID, db: Session = Depends(get_db)):
    """Launches a real, visible Chromium window on this machine's desktop,
    fills it with the same logic as the unattended prefill, and leaves it open
    for you to review and submit yourself. Returns immediately - the window
    opens in its own background process (see open_application_live)."""
    application = (
        db.query(Application).filter(Application.id == application_id, Application.user_id == user_id).first()
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    open_application_live(str(application_id))
    return {"status": "opening"}


@router.get("/users/{user_id}/applications/{application_id}/screenshot")
def get_application_screenshot(user_id: uuid.UUID, application_id: uuid.UUID, db: Session = Depends(get_db)):
    application = db.query(Application).filter(Application.id == application_id, Application.user_id == user_id).first()
    if not application or not application.screenshot_path:
        raise HTTPException(status_code=404, detail="No screenshot available")
    return FileResponse(application.screenshot_path)


@router.patch("/users/{user_id}/applications/{application_id}/mark-submitted", response_model=ApplicationOut)
def mark_submitted(
    user_id: uuid.UUID, application_id: uuid.UUID, payload: ApplicationMarkSubmitted, db: Session = Depends(get_db)
):
    application = db.query(Application).filter(Application.id == application_id, Application.user_id == user_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if payload.resume_id:
        application.resume_id = payload.resume_id
    application.status = ApplicationStatus.APPLIED
    application.applied_at = datetime.utcnow()
    db.commit()
    db.refresh(application)
    return application


@router.post("/run-cycle")
def run_cycle():
    """Called every 6 hours by the n8n workflow, right after job-source discovery.

    For every user: match newly discovered postings, pre-fill applications for
    anything that matched, and email them about missing info / what's ready to review.
    """
    db = SessionLocal()
    summary = []
    try:
        users = db.query(User).all()
        for user in users:
            applications = match_all_unmatched_jobs_for_user(db, user)
            prefilled = 0
            for application in applications:
                if application.status not in (ApplicationStatus.AUTO_APPLIED, ApplicationStatus.PENDING_REVIEW):
                    continue
                try:
                    result = prefill_application(db, application)
                except Exception:
                    import traceback
                    application.status = ApplicationStatus.FAILED
                    application.review_reason = f"Pre-fill failed: {traceback.format_exc()}"
                    db.commit()
                    continue

                _store_prefill_result_and_notify(db, application, user, result)
                prefilled += 1

            missing_profile_items = get_missing_profile_items(db, user)
            if missing_profile_items:
                send_email(
                    db, user.id, user.email,
                    subject="Your job application profile is missing some info",
                    body=(
                        "These items are missing from your profile, which may block auto-applying:\n\n"
                        + "\n".join(f"- {item}" for item in missing_profile_items)
                    ),
                )

            summary.append({"user_id": str(user.id), "matched": len(applications), "prefilled": prefilled})
    finally:
        db.close()
    return summary

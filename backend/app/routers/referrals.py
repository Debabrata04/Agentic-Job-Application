"""Referral-contact suggestions, sourced from the user's OWN official
LinkedIn data export (Settings > Data Privacy > "Get a copy of your data" >
Connections CSV) - never from logging into LinkedIn or scraping it. LinkedIn
has no public API for reading a member's connections, and automating a
login/browser session to read them violates LinkedIn's ToS and risks an
account ban, even for personal use. This CSV is the ToS-compliant path to
the same data: it's an export LinkedIn itself generates for the user.

Flow: user uploads the CSV here -> we store/update their connections table
-> match connection companies against job_postings.company (case-insensitive)
-> email the user the resulting list so THEY can decide who to reach out to.
We never contact a connection directly - only the account owner gets emailed.
"""

import csv
import io
import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Education, JobPosting, LinkedInConnection, Resume, User, WorkExperience
from app.schemas.schemas import (
    LinkedInConnectionIn,
    ReferralImportResult,
    ReferralMatchOut,
    ReferralMessageOut,
    ReferralMessageRequest,
)
from app.services.email_notifier import send_email
from app.services.gemini_filler import draft_referral_message

router = APIRouter(prefix="/users/{user_id}/referrals", tags=["referrals"])

# LinkedIn's export CSV has a few boilerplate lines before the real header
# ("Notes:", a blurb about the export, a blank line) - skip down to the row
# that actually looks like the header before handing off to csv.DictReader.
_HEADER_MARKERS = ("First Name", "Last Name")


def _parse_connections_csv(raw_bytes: bytes) -> list[dict]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_idx = 0
    for i, line in enumerate(lines):
        if all(marker in line for marker in _HEADER_MARKERS):
            header_idx = i
            break
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    return list(reader)


def _parse_connected_on(value: str):
    if not value:
        return None
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


@router.post("/import", response_model=ReferralImportResult, status_code=201)
def import_connections(user_id: uuid.UUID, file: UploadFile, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not file:
        raise HTTPException(status_code=400, detail="A connections CSV file is required")

    rows = _parse_connections_csv(file.file.read())

    imported = 0
    skipped = 0
    for row in rows:
        url = (row.get("URL") or row.get("Profile URL") or "").strip()
        first_name = (row.get("First Name") or "").strip()
        last_name = (row.get("Last Name") or "").strip()
        company = (row.get("Company") or "").strip()
        if not url and not (first_name or last_name):
            skipped += 1
            continue

        existing = None
        if url:
            existing = (
                db.query(LinkedInConnection)
                .filter(LinkedInConnection.user_id == user_id, LinkedInConnection.url == url)
                .first()
            )
        connection = existing or LinkedInConnection(user_id=user_id, url=url or None)
        connection.first_name = first_name or None
        connection.last_name = last_name or None
        connection.company = company or None
        connection.position = (row.get("Position") or "").strip() or None
        connection.email_address = (row.get("Email Address") or "").strip() or None
        connection.connected_on = _parse_connected_on(row.get("Connected On") or "")
        db.add(connection)
        imported += 1

    db.commit()

    matches = _match_connections_to_postings(db, user_id)
    email_sent = _email_referral_matches(db, user, matches) if matches else False

    return ReferralImportResult(imported=imported, skipped=skipped, matches=matches, email_sent=email_sent)


@router.post("/manual", response_model=ReferralImportResult, status_code=201)
def add_connection_manually(user_id: uuid.UUID, connection_in: LinkedInConnectionIn, db: Session = Depends(get_db)):
    """Faster alternative to the CSV import for a handful of contacts - add
    someone you remember from LinkedIn's "My Network" page by hand instead of
    waiting on the data-export email. Still no scraping/login involved."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not connection_in.company.strip():
        raise HTTPException(status_code=400, detail="Company is required")

    connection = LinkedInConnection(
        user_id=user_id,
        first_name=(connection_in.first_name or "").strip() or None,
        last_name=(connection_in.last_name or "").strip() or None,
        company=connection_in.company.strip(),
        position=(connection_in.position or "").strip() or None,
        url=(connection_in.url or "").strip() or None,
    )
    db.add(connection)
    db.commit()

    matches = _match_connections_to_postings(db, user_id)
    email_sent = _email_referral_matches(db, user, matches) if matches else False

    return ReferralImportResult(imported=1, skipped=0, matches=matches, email_sent=email_sent)


def _build_candidate_background(db: Session, user: User) -> str:
    """Plain-text summary of the candidate's education/current role/
    achievements for Gemini to draft a referral message from - same
    "only real facts, nothing invented" approach as application_filler's
    own candidate context, built from the structured profile rows (not the
    resume parse) since education/work history are already clean, dated
    fields here."""
    lines = [f"Name: {user.full_name}"]

    educations = (
        db.query(Education)
        .filter(Education.user_id == user.id)
        .order_by(Education.end_date.desc().nullsfirst())
        .all()
    )
    for edu in educations:
        degree = f"{edu.degree} in {edu.field_of_study}" if edu.degree and edu.field_of_study else (edu.degree or edu.field_of_study or "")
        lines.append(f"Education: {degree} at {edu.school}".strip())

    work_experiences = (
        db.query(WorkExperience)
        .filter(WorkExperience.user_id == user.id)
        .order_by(WorkExperience.is_current.desc(), WorkExperience.start_date.desc().nullsfirst())
        .all()
    )
    for exp in work_experiences:
        status = "Current role" if exp.is_current else "Past role"
        lines.append(f"{status}: {exp.title} at {exp.company}")
        if exp.description:
            lines.append(f"  Achievements/responsibilities: {exp.description.strip()[:600]}")

    resume = (
        db.query(Resume)
        .filter(Resume.user_id == user.id, Resume.is_active.is_(True))
        .order_by(Resume.created_at.desc())
        .first()
    )
    if resume and resume.parsed_text:
        lines.append("\nResume excerpt (for additional achievements/skills only):")
        lines.append(resume.parsed_text[:3000])

    return "\n".join(lines)


def _build_job_context(posting: JobPosting) -> tuple[str, str]:
    """Returns (job_id, job_context_text). job_id is whatever the ATS itself
    calls this posting (external_id, e.g. a Greenhouse gh_jid) - falls back
    to our own row id only if the source never gave us one, since that's
    the id a connection could actually look up on the company's board."""
    job_id = posting.external_id or str(posting.id)
    lines = [
        f"Job ID: {job_id}",
        f"Title: {posting.title}",
        f"Company: {posting.company}",
    ]
    if posting.location:
        lines.append(f"Location: {posting.location}")
    if posting.description:
        lines.append(f"Description excerpt: {posting.description.strip()[:1500]}")
    return job_id, "\n".join(lines)


@router.post("/draft-message", response_model=ReferralMessageOut)
def draft_message(user_id: uuid.UUID, request: ReferralMessageRequest, db: Session = Depends(get_db)):
    """Generates referral-request message TEXT only - nothing is sent, typed
    into LinkedIn, or delivered to the connection from here. The user reviews
    and sends it themselves (see the extension's draft-fill feature, which
    only fills an already-open LinkedIn message box, never clicks Send)."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    connection = (
        db.query(LinkedInConnection)
        .filter(LinkedInConnection.id == request.connection_id, LinkedInConnection.user_id == user_id)
        .first()
    )
    if not connection:
        raise HTTPException(status_code=404, detail="Connection not found")

    posting = db.get(JobPosting, request.job_posting_id)
    if not posting:
        raise HTTPException(status_code=404, detail="Job posting not found")

    connection_name = f"{connection.first_name or ''} {connection.last_name or ''}".strip()
    candidate_context = _build_candidate_background(db, user)
    job_id, job_context = _build_job_context(posting)

    message = draft_referral_message(candidate_context, job_context, connection_name)
    if not message:
        raise HTTPException(status_code=502, detail="Could not draft a message right now (Gemini unavailable or not configured) - try again shortly")

    return ReferralMessageOut(
        connection_id=connection.id,
        job_posting_id=posting.id,
        job_id=job_id,
        message=message,
    )


@router.get("/matches", response_model=list[ReferralMatchOut])
def get_referral_matches(user_id: uuid.UUID, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return _match_connections_to_postings(db, user_id)


def _match_connections_to_postings(db: Session, user_id: uuid.UUID) -> list[ReferralMatchOut]:
    connections = (
        db.query(LinkedInConnection)
        .filter(LinkedInConnection.user_id == user_id, LinkedInConnection.company.isnot(None))
        .all()
    )
    if not connections:
        return []

    connections_by_company = defaultdict(list)
    for c in connections:
        connections_by_company[c.company.strip().lower()].append(c)

    postings = db.query(JobPosting).filter(JobPosting.company.isnot(None)).all()
    postings_by_company = defaultdict(list)
    for p in postings:
        postings_by_company[p.company.strip().lower()].append(p)

    matches = []
    for company_key, conns in connections_by_company.items():
        matched_postings = postings_by_company.get(company_key)
        if not matched_postings:
            continue
        matches.append(
            ReferralMatchOut(
                company=conns[0].company,
                connections=conns,
                job_postings=matched_postings,
            )
        )
    matches.sort(key=lambda m: m.company.lower())
    return matches


def _email_referral_matches(db: Session, user: User, matches: list[ReferralMatchOut]) -> bool:
    lines = [
        "Your LinkedIn connections include people at companies you have open job postings for.",
        "Here are the matches - reach out yourself if you'd like to ask for a referral:",
        "",
    ]
    for match in matches:
        lines.append(f"{match.company}:")
        for c in match.connections:
            name = f"{c.first_name or ''} {c.last_name or ''}".strip() or "(name unknown)"
            position = f" - {c.position}" if c.position else ""
            profile = f" ({c.url})" if c.url else ""
            lines.append(f"  - {name}{position}{profile}")
        for p in match.job_postings:
            lines.append(f"  Open role: {p.title} ({p.url or 'no link'})")
        lines.append("")

    body = "\n".join(lines)
    return send_email(db, user.id, user.email, "Potential referral contacts for your open applications", body)

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Resume, ResumeSection, User
from app.schemas.schemas import ResumeOut
from app.services.resume_parsing import extract_text
from app.services.resume_pdf_structure import get_header_boundary_lines
from app.services.resume_sections import split_into_sections
from app.services.resume_skills import extract_tech_stack
from app.services.storage import delete_file, save_resume

router = APIRouter(prefix="/users/{user_id}/resumes", tags=["resumes"])


def _process_sections_and_skills(db: Session, resume: Resume) -> None:
    """Splits parsed_text into sections, replaces any existing ResumeSection
    rows for this resume (so reparse doesn't duplicate), then extracts the
    tech stack from those sections and stores it on the resume. Runs once per
    upload/reparse - never recomputed per-match, see resume_skills.py.

    For PDF resumes, also runs a font-signature pass (resume_pdf_structure)
    over the original file to find section-boundary lines that the
    text-pattern heuristic alone might not recognize as headers (e.g.
    "Patent") but which share the exact styling of this resume's own real
    headers - DOCX/other formats fall back to text-pattern-only detection,
    since PyMuPDF only reads PDF font metadata."""
    db.query(ResumeSection).filter(ResumeSection.resume_id == resume.id).delete()

    extra_boundary_lines = None
    if resume.file_path.lower().endswith(".pdf"):
        extra_boundary_lines = get_header_boundary_lines(resume.file_path)

    sections = split_into_sections(resume.parsed_text, extra_boundary_lines=extra_boundary_lines)
    for section_type, content in sections.items():
        db.add(ResumeSection(resume_id=resume.id, section_type=section_type, content=content))

    resume.extracted_skills = extract_tech_stack(resume.parsed_text, extra_boundary_lines=extra_boundary_lines)


@router.post("", response_model=ResumeOut, status_code=201)
def upload_resume(
    user_id: uuid.UUID,
    label: str = Form(...),
    years_of_experience: float | None = Form(None),
    file: UploadFile = None,
    db: Session = Depends(get_db),
):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not file:
        raise HTTPException(status_code=400, detail="A resume file is required")

    stored_path, original_filename = save_resume(file, str(user_id))

    resume = Resume(
        user_id=user_id,
        label=label,
        years_of_experience=years_of_experience,
        file_path=stored_path,
        original_filename=original_filename,
        parsed_text=extract_text(stored_path),
    )
    db.add(resume)
    db.flush()  # assigns resume.id without committing, needed for ResumeSection FKs below
    _process_sections_and_skills(db, resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("", response_model=list[ResumeOut])
def list_resumes(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Resume).filter(Resume.user_id == user_id).all()


@router.get("/{resume_id}/download")
def download_resume(user_id: uuid.UUID, resume_id: uuid.UUID, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return FileResponse(resume.file_path, filename=resume.original_filename)


@router.patch("/{resume_id}", response_model=ResumeOut)
def update_resume(
    user_id: uuid.UUID,
    resume_id: uuid.UUID,
    label: str = Form(...),
    years_of_experience: float | None = Form(None),
    db: Session = Depends(get_db),
):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    resume.label = label
    resume.years_of_experience = years_of_experience
    db.commit()
    db.refresh(resume)
    return resume


@router.patch("/{resume_id}/activate", response_model=ResumeOut)
def set_resume_active(user_id: uuid.UUID, resume_id: uuid.UUID, is_active: bool, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    resume.is_active = is_active
    db.commit()
    db.refresh(resume)
    return resume


@router.post("/{resume_id}/reparse", response_model=ResumeOut)
def reparse_resume(user_id: uuid.UUID, resume_id: uuid.UUID, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    resume.parsed_text = extract_text(resume.file_path)
    _process_sections_and_skills(db, resume)
    db.commit()
    db.refresh(resume)
    return resume


@router.delete("/{resume_id}", status_code=204)
def delete_resume(user_id: uuid.UUID, resume_id: uuid.UUID, db: Session = Depends(get_db)):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    delete_file(resume.file_path)
    db.delete(resume)
    db.commit()

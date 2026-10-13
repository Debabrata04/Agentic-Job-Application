import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Resume, User
from app.schemas.schemas import ResumeOut
from app.services.resume_parsing import extract_text
from app.services.storage import delete_file, save_resume

router = APIRouter(prefix="/users/{user_id}/resumes", tags=["resumes"])


@router.post("", response_model=ResumeOut, status_code=201)
def upload_resume(
    user_id: uuid.UUID,
    label: str = Form(...),
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
        file_path=stored_path,
        original_filename=original_filename,
        parsed_text=extract_text(stored_path),
    )
    db.add(resume)
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
    db: Session = Depends(get_db),
):
    resume = db.query(Resume).filter(Resume.id == resume_id, Resume.user_id == user_id).first()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    resume.label = label
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

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Education, User, WorkExperience
from app.schemas.schemas import (
    EducationIn,
    EducationOut,
    EducationUpdate,
    WorkExperienceIn,
    WorkExperienceOut,
    WorkExperienceUpdate,
)

router = APIRouter(tags=["work-history"])


@router.post("/users/{user_id}/work-experiences", response_model=WorkExperienceOut, status_code=201)
def add_work_experience(user_id: uuid.UUID, payload: WorkExperienceIn, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    entry = WorkExperience(user_id=user_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/users/{user_id}/work-experiences", response_model=list[WorkExperienceOut])
def list_work_experiences(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(WorkExperience).filter(WorkExperience.user_id == user_id).all()


@router.patch("/users/{user_id}/work-experiences/{entry_id}", response_model=WorkExperienceOut)
def update_work_experience(
    user_id: uuid.UUID, entry_id: uuid.UUID, payload: WorkExperienceUpdate, db: Session = Depends(get_db)
):
    entry = db.query(WorkExperience).filter(WorkExperience.id == entry_id, WorkExperience.user_id == user_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Work experience not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/users/{user_id}/work-experiences/{entry_id}", status_code=204)
def delete_work_experience(user_id: uuid.UUID, entry_id: uuid.UUID, db: Session = Depends(get_db)):
    entry = db.query(WorkExperience).filter(WorkExperience.id == entry_id, WorkExperience.user_id == user_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Work experience not found")
    db.delete(entry)
    db.commit()


@router.post("/users/{user_id}/educations", response_model=EducationOut, status_code=201)
def add_education(user_id: uuid.UUID, payload: EducationIn, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    entry = Education(user_id=user_id, **payload.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.get("/users/{user_id}/educations", response_model=list[EducationOut])
def list_educations(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Education).filter(Education.user_id == user_id).all()


@router.patch("/users/{user_id}/educations/{entry_id}", response_model=EducationOut)
def update_education(
    user_id: uuid.UUID, entry_id: uuid.UUID, payload: EducationUpdate, db: Session = Depends(get_db)
):
    entry = db.query(Education).filter(Education.id == entry_id, Education.user_id == user_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Education not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, key, value)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/users/{user_id}/educations/{entry_id}", status_code=204)
def delete_education(user_id: uuid.UUID, entry_id: uuid.UUID, db: Session = Depends(get_db)):
    entry = db.query(Education).filter(Education.id == entry_id, Education.user_id == user_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Education not found")
    db.delete(entry)
    db.commit()

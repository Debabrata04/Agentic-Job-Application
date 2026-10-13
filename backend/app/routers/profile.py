import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import CandidateProfile, User
from app.schemas.schemas import CandidateProfileIn, CandidateProfileOut

router = APIRouter(prefix="/users/{user_id}/profile", tags=["profile"])


def _get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("", response_model=CandidateProfileOut)
def get_profile(user_id: uuid.UUID, db: Session = Depends(get_db)):
    _get_user_or_404(db, user_id)
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not set up yet")
    return profile


@router.put("", response_model=CandidateProfileOut)
def upsert_profile(user_id: uuid.UUID, payload: CandidateProfileIn, db: Session = Depends(get_db)):
    _get_user_or_404(db, user_id)
    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user_id).first()
    data = payload.model_dump()
    if profile:
        for key, value in data.items():
            setattr(profile, key, value)
    else:
        profile = CandidateProfile(user_id=user_id, **data)
        db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import TargetRole, User
from app.schemas.schemas import TargetRoleIn, TargetRoleOut

router = APIRouter(prefix="/users/{user_id}/target-roles", tags=["target-roles"])


@router.post("", response_model=TargetRoleOut, status_code=201)
def create_target_role(user_id: uuid.UUID, payload: TargetRoleIn, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    role = TargetRole(user_id=user_id, **payload.model_dump())
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


@router.get("", response_model=list[TargetRoleOut])
def list_target_roles(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(TargetRole).filter(TargetRole.user_id == user_id).all()


@router.patch("/{role_id}", response_model=TargetRoleOut)
def update_target_role(user_id: uuid.UUID, role_id: uuid.UUID, payload: TargetRoleIn, db: Session = Depends(get_db)):
    role = db.query(TargetRole).filter(TargetRole.id == role_id, TargetRole.user_id == user_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Target role not found")
    for key, value in payload.model_dump().items():
        setattr(role, key, value)
    db.commit()
    db.refresh(role)
    return role


@router.delete("/{role_id}", status_code=204)
def delete_target_role(user_id: uuid.UUID, role_id: uuid.UUID, db: Session = Depends(get_db)):
    role = db.query(TargetRole).filter(TargetRole.id == role_id, TargetRole.user_id == user_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Target role not found")
    db.delete(role)
    db.commit()

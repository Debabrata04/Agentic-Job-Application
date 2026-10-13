import uuid
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.models import Document, User
from app.schemas.schemas import DocumentOut
from app.services.storage import delete_file, save_document

router = APIRouter(prefix="/users/{user_id}/documents", tags=["documents"])


@router.post("", response_model=DocumentOut, status_code=201)
def upload_document(
    user_id: uuid.UUID,
    doc_type: str = Form(...),
    file: UploadFile = None,
    db: Session = Depends(get_db),
):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if not file:
        raise HTTPException(status_code=400, detail="A document file is required")

    stored_path, original_filename = save_document(file, str(user_id))

    document = Document(
        user_id=user_id,
        doc_type=doc_type,
        file_path=stored_path,
        original_filename=original_filename,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("", response_model=list[DocumentOut])
def list_documents(user_id: uuid.UUID, db: Session = Depends(get_db)):
    return db.query(Document).filter(Document.user_id == user_id).all()


@router.get("/{document_id}/download")
def download_document(user_id: uuid.UUID, document_id: uuid.UUID, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id, Document.user_id == user_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(document.file_path, filename=document.original_filename)


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    doc_type: str = Form(None),
    file: UploadFile = None,
    db: Session = Depends(get_db),
):
    document = db.query(Document).filter(Document.id == document_id, Document.user_id == user_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc_type is not None:
        document.doc_type = doc_type

    if file is not None:
        delete_file(document.file_path)
        stored_path, original_filename = save_document(file, str(user_id))
        document.file_path = stored_path
        document.original_filename = original_filename

    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=204)
def delete_document(user_id: uuid.UUID, document_id: uuid.UUID, db: Session = Depends(get_db)):
    document = db.query(Document).filter(Document.id == document_id, Document.user_id == user_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_file(document.file_path)
    db.delete(document)
    db.commit()

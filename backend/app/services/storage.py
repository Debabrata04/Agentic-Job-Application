import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings


def _save_file(upload: UploadFile, base_dir: str, subdir: str) -> tuple[str, str]:
    target_dir = Path(base_dir) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(upload.filename or "").suffix
    stored_name = f"{uuid.uuid4()}{ext}"
    stored_path = target_dir / stored_name

    with open(stored_path, "wb") as f:
        f.write(upload.file.read())

    return str(stored_path), upload.filename or stored_name


def save_resume(upload: UploadFile, user_id: str) -> tuple[str, str]:
    return _save_file(upload, settings.resume_storage_dir, user_id)


def save_document(upload: UploadFile, user_id: str) -> tuple[str, str]:
    return _save_file(upload, settings.document_storage_dir, user_id)


def delete_file(file_path: str) -> None:
    path = Path(file_path)
    path.unlink(missing_ok=True)

from pathlib import Path

from pydantic_settings import BaseSettings

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://job_agent:job_agent_dev_password@localhost:5432/job_agent"
    resume_storage_dir: str = str(PROJECT_ROOT / "storage" / "resumes")
    document_storage_dir: str = str(PROJECT_ROOT / "storage" / "documents")
    screenshot_storage_dir: str = str(PROJECT_ROOT / "storage" / "screenshots")

    # Gmail SMTP (use a Gmail App Password, not the account password)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_app_password: str = ""
    notification_from_email: str = ""

    # The backend's own publicly-reachable base URL, used to build links sent in
    # emails (e.g. the live-fill launch link). Must point wherever the backend
    # actually runs - localhost for local dev, the EC2 Elastic IP in production.
    public_base_url: str = "http://localhost:8000"

    # Free-tier key from Google AI Studio (makersuite.google.com/app/apikey).
    # Used as a fallback, not the primary matcher - see gemini_filler.py. Left
    # empty, the fallback is simply skipped (fields it would've caught stay in
    # missing_fields, same as before this feature existed).
    gemini_api_key: str = ""

    class Config:
        env_file = ".env"


settings = Settings()

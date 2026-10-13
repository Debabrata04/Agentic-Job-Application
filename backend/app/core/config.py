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

    class Config:
        env_file = ".env"


settings = Settings()

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    years_of_experience: Optional[float] = None


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    years_of_experience: Optional[float] = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    years_of_experience: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CandidateProfileIn(BaseModel):
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    github_url: Optional[str] = None
    work_authorization: Optional[str] = None
    requires_sponsorship: Optional[bool] = None
    willing_to_relocate: Optional[bool] = None
    gender: Optional[str] = None
    race_ethnicity: Optional[str] = None
    veteran_status: Optional[str] = None
    disability_status: Optional[str] = None
    expected_salary_min: Optional[int] = None
    expected_salary_max: Optional[int] = None
    notice_period_days: Optional[int] = None
    extra_fields: Optional[dict] = None


class CandidateProfileOut(CandidateProfileIn):
    id: uuid.UUID
    user_id: uuid.UUID

    class Config:
        from_attributes = True


class TargetRoleIn(BaseModel):
    title: str
    min_years_experience: Optional[float] = None
    max_years_experience: Optional[float] = None
    keywords: Optional[list[str]] = None
    preferred_resume_id: Optional[uuid.UUID] = None
    is_active: bool = True


class TargetRoleOut(TargetRoleIn):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class ResumeOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    label: str
    original_filename: str
    is_active: bool
    parsed_text: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WorkExperienceIn(BaseModel):
    company: str
    title: str
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: bool = False
    description: Optional[str] = None


class WorkExperienceOut(WorkExperienceIn):
    id: uuid.UUID
    user_id: uuid.UUID

    class Config:
        from_attributes = True


class WorkExperienceUpdate(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: Optional[bool] = None
    description: Optional[str] = None


class EducationIn(BaseModel):
    school: str
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    gpa: Optional[float] = None


class EducationOut(EducationIn):
    id: uuid.UUID
    user_id: uuid.UUID

    class Config:
        from_attributes = True


class EducationUpdate(BaseModel):
    school: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    gpa: Optional[float] = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    doc_type: str
    original_filename: str
    created_at: datetime

    class Config:
        from_attributes = True


class JobSourceIn(BaseModel):
    company_name: str
    platform: str  # "greenhouse", "lever", "smartrecruiters"
    slug: str
    is_active: bool = True


class JobSourceUpdate(BaseModel):
    company_name: Optional[str] = None
    slug: Optional[str] = None
    is_active: Optional[bool] = None


class JobSourceOut(BaseModel):
    id: uuid.UUID
    company_name: str
    platform: str
    slug: str
    is_active: bool
    last_polled_at: Optional[datetime] = None
    last_poll_status: Optional[str] = None
    last_poll_error: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class JobPostingOut(BaseModel):
    id: uuid.UUID
    source: str
    external_id: Optional[str] = None
    company: str
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    contact_email: Optional[str] = None
    discovered_at: datetime

    class Config:
        from_attributes = True


class ApplicationOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    job_posting_id: uuid.UUID
    resume_id: uuid.UUID
    target_role_id: Optional[uuid.UUID] = None
    similarity_score: Optional[float] = None
    status: str
    review_reason: Optional[str] = None
    matched_skills: Optional[list[str]] = None
    missing_skills: Optional[list[str]] = None
    filled_answers: Optional[dict] = None
    missing_fields: Optional[list[str]] = None
    optional_missing_fields: Optional[list[str]] = None
    screenshot_path: Optional[str] = None
    prefilled_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ApplicationMarkSubmitted(BaseModel):
    resume_id: Optional[uuid.UUID] = None

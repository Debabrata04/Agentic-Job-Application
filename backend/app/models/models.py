import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


def gen_uuid():
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    full_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    years_of_experience = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    profile = relationship("CandidateProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    target_roles = relationship("TargetRole", back_populates="user", cascade="all, delete-orphan")
    resumes = relationship("Resume", back_populates="user", cascade="all, delete-orphan")
    work_experiences = relationship("WorkExperience", back_populates="user", cascade="all, delete-orphan")
    educations = relationship("Education", back_populates="user", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="user", cascade="all, delete-orphan")
    applications = relationship("Application", back_populates="user", cascade="all, delete-orphan")


class CandidateProfile(Base):
    """Workday-style fields reused across applications: address, work auth, EEO, demographics."""

    __tablename__ = "candidate_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)

    address_line1 = Column(String, nullable=True)
    address_line2 = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)
    country = Column(String, nullable=True)

    linkedin_url = Column(String, nullable=True)
    portfolio_url = Column(String, nullable=True)
    github_url = Column(String, nullable=True)

    work_authorization = Column(String, nullable=True)  
    requires_sponsorship = Column(Boolean, nullable=True)
    willing_to_relocate = Column(Boolean, nullable=True)

    gender = Column(String, nullable=True)
    race_ethnicity = Column(String, nullable=True)
    veteran_status = Column(String, nullable=True)
    disability_status = Column(String, nullable=True)

    expected_salary_min = Column(Integer, nullable=True)
    expected_salary_max = Column(Integer, nullable=True)
    notice_period_days = Column(Integer, nullable=True)

    extra_fields = Column(JSONB, nullable=True) 
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="profile")


class TargetRole(Base):
    """Roles/titles the user wants to be matched against, with an optional preferred resume."""

    __tablename__ = "target_roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)  # e.g. "Backend Engineer"
    min_years_experience = Column(Float, nullable=True)  # required range on postings to match
    max_years_experience = Column(Float, nullable=True)
    # The user's OWN self-reported years of experience for THIS role specifically
    # (distinct from min/max above, which describe what postings to look for, and
    # from User.years_of_experience, which is a single global figure). Falls back
    # to User.years_of_experience when not set - see matching.py.
    years_of_experience = Column(Float, nullable=True)
    keywords = Column(JSONB, nullable=True)  # list of must-have/nice-to-have keywords
    preferred_resume_id = Column(UUID(as_uuid=True), ForeignKey("resumes.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="target_roles")
    preferred_resume = relationship("Resume", foreign_keys=[preferred_resume_id])


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    label = Column(String, nullable=False)  # e.g. "Backend - Python focus"
    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    parsed_text = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    # Manually entered by the user (never auto-extracted from resume text,
    # which is unreliable) - the YOE used for the first-pass screening gate in
    # matching.py, compared against each JD's Gemini-extracted requirement.
    years_of_experience = Column(Float, nullable=True)
    # Tech stack extracted from this resume's Skills/Projects/Experience sections
    # via jjzha/jobbert_skill_extraction (see resume_skills.py), computed once on
    # upload and reused for every subsequent match - not recomputed per-match.
    extracted_skills = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="resumes")
    sections = relationship("ResumeSection", back_populates="resume", cascade="all, delete-orphan")


class ResumeSection(Base):
    """Regex-header-split chunks of a resume's text (Skills/Projects/Experience/
    Education). Stored separately so skill extraction can run against just these
    sections, not the whole resume blob (education/contact info add noise).
    Cascade-deletes with the resume - deleting a resume wipes its sections too."""

    __tablename__ = "resume_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    resume_id = Column(UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False)
    section_type = Column(String, nullable=False)  # "skills", "projects", "experience", "education"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    resume = relationship("Resume", back_populates="sections")


class WorkExperience(Base):
    __tablename__ = "work_experiences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    company = Column(String, nullable=False)
    title = Column(String, nullable=False)
    location = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_current = Column(Boolean, default=False)
    description = Column(Text, nullable=True)

    user = relationship("User", back_populates="work_experiences")


class Education(Base):
    __tablename__ = "educations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    school = Column(String, nullable=False)
    degree = Column(String, nullable=True)
    field_of_study = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    gpa = Column(Float, nullable=True)

    user = relationship("User", back_populates="educations")


class Document(Base):
    """Supporting documents: transcripts, certifications, visa/work-auth proof, etc."""

    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    doc_type = Column(String, nullable=False)  # "transcript", "cover_letter", "visa_proof", etc.
    file_path = Column(String, nullable=False)
    original_filename = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")


class JobSource(Base):
    """Registry of companies to poll for job postings — one row per (company, platform)."""

    __tablename__ = "job_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    company_name = Column(String, nullable=False)
    platform = Column(String, nullable=False)  # "greenhouse", "lever", "smartrecruiters"
    slug = Column(String, nullable=False)  # the company identifier the platform's API expects
    is_active = Column(Boolean, default=True)
    last_polled_at = Column(DateTime, nullable=True)
    last_poll_status = Column(String, nullable=True)  # "ok", "error"
    last_poll_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("platform", "slug", name="uq_job_sources_platform_slug"),)


class JobPosting(Base):
    __tablename__ = "job_postings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source = Column(String, nullable=False)  # "greenhouse", "lever", "linkedin_email", etc.
    external_id = Column(String, nullable=True)
    company = Column(String, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    location = Column(String, nullable=True)
    url = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)  # for LinkedIn-post-style email applications
    discovered_at = Column(DateTime, default=datetime.utcnow)

    # Auto-maintained by Postgres itself (GENERATED ALWAYS AS ... STORED) - never
    # written to directly, always reflects the current title/description. Used
    # for BM25-style ranked keyword search via ts_rank (see job_search.py); not
    # literal BM25 (that needs the pg_search extension, which doesn't support
    # native Windows Postgres - see project history), but Postgres's own
    # built-in full-text ranking, with zero extra infrastructure.
    # regexp_replace(..., '/', ' ', 'g') matters: Postgres's tsvector parser
    # treats a slash-joined pair like "PyTorch/TensorFlow" as ONE compound
    # lexeme ("pytorch/tensorflow"), not two separate searchable words - this
    # silently hid real postings from keyword search whenever a JD listed a
    # skill as half of a slash-separated pair (e.g. "PyTorch/TensorFlow",
    # "LangChain/LangGraph"). See migration f6bb0e0bb3c5 for the fix history.
    search_vector = Column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', regexp_replace(coalesce(title, '') || ' ' || "
            "coalesce(description, ''), '/', ' ', 'g'))",
            persisted=True,
        ),
    )

    # Gemini-extracted JD requirements, computed once per posting (not once per
    # resume/user/cycle) and cached here - see matching.py's first-pass YOE/
    # profession screen, which runs before the expensive cross-encoder stage.
    # gemini_screened distinguishes "tried, found nothing" from "never tried",
    # so a failed/unconfigured Gemini call isn't retried every single cycle.
    gemini_required_years = Column(Float, nullable=True)
    gemini_related_professions = Column(JSONB, nullable=True)
    gemini_screened = Column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_postings_source_external_id"),
        Index("ix_job_postings_search_vector", "search_vector", postgresql_using="gin"),
    )

    applications = relationship("Application", back_populates="job_posting")


class ApplicationStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    AUTO_APPLIED = "auto_applied"
    APPLIED = "applied"
    NEEDS_MORE_INFO = "needs_more_info"
    REJECTED_BY_USER = "rejected_by_user"
    FAILED = "failed"


class Application(Base):
    __tablename__ = "applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    job_posting_id = Column(UUID(as_uuid=True), ForeignKey("job_postings.id"), nullable=False)
    resume_id = Column(UUID(as_uuid=True), ForeignKey("resumes.id"), nullable=False)
    target_role_id = Column(UUID(as_uuid=True), ForeignKey("target_roles.id"), nullable=True)

    similarity_score = Column(Float, nullable=True)
    status = Column(Enum(ApplicationStatus), default=ApplicationStatus.PENDING_REVIEW, nullable=False)
    review_reason = Column(String, nullable=True)  # why it needed human review
    matched_skills = Column(JSONB, nullable=True)  # skills in both your resume/target roles and the JD
    missing_skills = Column(JSONB, nullable=True)  # skills the JD mentions that your profile doesn't

    job_description_snapshot = Column(Text, nullable=True)
    filled_answers = Column(JSONB, nullable=True)  # field label -> value, as filled by the Playwright prefiller
    missing_fields = Column(JSONB, nullable=True)  # required labels the prefiller found on the form but had no data for
    optional_missing_fields = Column(JSONB, nullable=True)  # same, but for non-required fields
    gemini_filled_fields = Column(JSONB, nullable=True)  # subset of filled_answers' keys filled via the Gemini fallback, not the keyword matcher
    screenshot_path = Column(String, nullable=True)  # pre-fill screenshot, for you to review before submitting
    prefilled_at = Column(DateTime, nullable=True)

    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "job_posting_id", name="uq_applications_user_job_posting"),)

    user = relationship("User", back_populates="applications")
    job_posting = relationship("JobPosting", back_populates="applications")
    resume = relationship("Resume")
    notifications = relationship("Notification", back_populates="application", cascade="all, delete-orphan")


class LinkedInConnection(Base):
    """Rows imported from the user's own official LinkedIn data export
    (Settings > Data Privacy > Get a copy of your data > "Connections" CSV).
    Never scraped or accessed via LinkedIn login - that violates LinkedIn's
    ToS and risks an account ban even for personal use. This is the user's
    own exported data, re-uploaded here just to match against job_postings
    companies for referral-contact suggestions."""

    __tablename__ = "linkedin_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    company = Column(String, nullable=True)
    position = Column(String, nullable=True)
    email_address = Column(String, nullable=True)
    url = Column(String, nullable=True)  # LinkedIn profile URL
    connected_on = Column(Date, nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("user_id", "url", name="uq_linkedin_connections_user_url"),)


class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    PUSH = "push"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=True)
    channel = Column(Enum(NotificationChannel), nullable=False)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String, default="pending")  # pending, sent, failed
    created_at = Column(DateTime, default=datetime.utcnow)

    application = relationship("Application", back_populates="notifications")

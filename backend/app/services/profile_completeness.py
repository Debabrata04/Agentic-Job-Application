from sqlalchemy.orm import Session

from app.models.models import CandidateProfile, Resume, User, WorkExperience

# Profile fields the auto-apply pipeline relies on most often. Keep this list
# aligned with the fields application_filler.py knows how to map onto a form.
REQUIRED_PROFILE_FIELDS = {
    "address_line1": "Address",
    "city": "City",
    "state": "State",
    "postal_code": "Postal code",
    "country": "Country",
    "work_authorization": "Work authorization",
}


def get_missing_profile_items(db: Session, user: User) -> list[str]:
    missing: list[str] = []

    if user.phone is None:
        missing.append("Phone number")
    if user.years_of_experience is None:
        missing.append("Years of experience")

    profile = db.query(CandidateProfile).filter(CandidateProfile.user_id == user.id).first()
    if not profile:
        missing.extend(REQUIRED_PROFILE_FIELDS.values())
    else:
        for field, label in REQUIRED_PROFILE_FIELDS.items():
            if not getattr(profile, field):
                missing.append(label)
        if profile.requires_sponsorship is None:
            missing.append("Sponsorship requirement")
        if profile.willing_to_relocate is None:
            missing.append("Relocation preference")

    has_active_resume = (
        db.query(Resume).filter(Resume.user_id == user.id, Resume.is_active.is_(True)).first() is not None
    )
    if not has_active_resume:
        missing.append("An active resume")

    has_work_experience = db.query(WorkExperience).filter(WorkExperience.user_id == user.id).first() is not None
    if not has_work_experience:
        missing.append("At least one work experience entry")

    return missing

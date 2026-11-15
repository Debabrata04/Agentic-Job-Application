"""add resume years_of_experience and job_posting gemini screening fields

Revision ID: 7f2a9c1d4b3e
Revises: 3a8b64283d61
Create Date: 2026-11-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "7f2a9c1d4b3e"
down_revision = "3a8b64283d61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("years_of_experience", sa.Float(), nullable=True))
    op.add_column("job_postings", sa.Column("gemini_required_years", sa.Float(), nullable=True))
    op.add_column("job_postings", sa.Column("gemini_related_professions", postgresql.JSONB(), nullable=True))
    op.add_column(
        "job_postings",
        sa.Column("gemini_screened", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("job_postings", "gemini_screened")
    op.drop_column("job_postings", "gemini_related_professions")
    op.drop_column("job_postings", "gemini_required_years")
    op.drop_column("resumes", "years_of_experience")

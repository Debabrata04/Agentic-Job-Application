"""fix search_vector to split slash-joined terms into separate words

Revision ID: f6bb0e0bb3c5
Revises: c58c16fe6ba5
Create Date: 2026-10-25 12:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f6bb0e0bb3c5'
down_revision: Union[str, None] = 'c58c16fe6ba5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_EXPR = "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))"

# Postgres's tsvector parser treats a slash-joined pair like "PyTorch/TensorFlow"
# as ONE compound lexeme ("pytorch/tensorflow"), not two separate searchable
# words - confirmed directly: to_tsvector('english', 'PyTorch/TensorFlow') produced
# only 'pytorch/tensorflow':1, no standalone 'pytorch' or 'tensorflow' lexeme.
# That silently hid real postings from BM25 search whenever a JD listed a skill
# as half of a slash-separated pair (very common JD phrasing: "PyTorch/TensorFlow",
# "LangChain/LangGraph", "NLP/LLMs"). Replacing "/" with a space before
# tokenizing makes both halves searchable on their own.
NEW_EXPR = (
    "to_tsvector('english', regexp_replace(coalesce(title, '') || ' ' || "
    "coalesce(description, ''), '/', ' ', 'g'))"
)


def upgrade() -> None:
    # Postgres can't ALTER a GENERATED expression in place - the column (and
    # its GIN index, which depends on it) has to be dropped and re-added.
    op.drop_index('ix_job_postings_search_vector', table_name='job_postings', postgresql_using='gin')
    op.drop_column('job_postings', 'search_vector')
    op.add_column(
        'job_postings',
        sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed(NEW_EXPR, persisted=True), nullable=True),
    )
    op.create_index('ix_job_postings_search_vector', 'job_postings', ['search_vector'], unique=False, postgresql_using='gin')


def downgrade() -> None:
    op.drop_index('ix_job_postings_search_vector', table_name='job_postings', postgresql_using='gin')
    op.drop_column('job_postings', 'search_vector')
    op.add_column(
        'job_postings',
        sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed(OLD_EXPR, persisted=True), nullable=True),
    )
    op.create_index('ix_job_postings_search_vector', 'job_postings', ['search_vector'], unique=False, postgresql_using='gin')

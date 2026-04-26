"""Add seo_intel table for Trends/Algorithm-Updates intelligence (Welle 3)

Revision ID: 003
Revises: 002
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seo_intel",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("seo_projects.id"), nullable=False, index=True),
        sa.Column("audit_id", sa.String(64), sa.ForeignKey("seo_audits.id"), nullable=True, index=True),
        sa.Column("source", sa.String(50), nullable=False),  # 'google_trends' | 'algorithm_feed'
        sa.Column("type", sa.String(50), nullable=False),    # 'rising_query' | 'top_query' | 'interest_change'
        sa.Column("query", sa.String(255)),
        sa.Column("score", sa.Float),
        sa.Column("metadata_json", sa.JSON),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_seo_intel_project_source", "seo_intel", ["project_id", "source"])

    with op.batch_alter_table("seo_projects") as batch:
        batch.add_column(sa.Column("intel_config", sa.JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("seo_projects") as batch:
        batch.drop_column("intel_config")
    op.drop_index("ix_seo_intel_project_source", table_name="seo_intel")
    op.drop_table("seo_intel")

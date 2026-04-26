"""Add auto-fix tracking columns to seo_issues + seo_projects

Revision ID: 002
Revises: 001
Create Date: 2026-04-26

Welle 2 of SEO-Autopilot Auto-Fix-Loop:
- seo_projects.auto_fix_enabled: per-project flag
- seo_projects.auto_fix_config: JSON config (whitelist overrides, push_to_remote, ...)
- seo_issues.fix_applied_at: timestamp wenn Fix angewendet wurde
- seo_issues.applied_by: 'claude_auto' / 'manual' / 'rolled_back'
- seo_issues.git_commit_hash: SHA des Fix-Commits
- seo_issues.fix_diff: unified diff des angewendeten Fixes
- seo_issues.fix_error: Stacktrace bei Apply-Failure
"""
from alembic import op
import sqlalchemy as sa


revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("seo_projects") as batch:
        batch.add_column(
            sa.Column(
                "auto_fix_enabled",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            )
        )
        batch.add_column(sa.Column("auto_fix_config", sa.JSON(), nullable=True))

    with op.batch_alter_table("seo_issues") as batch:
        batch.add_column(sa.Column("fix_applied_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("applied_by", sa.String(50), nullable=True))
        batch.add_column(sa.Column("git_commit_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("fix_diff", sa.Text(), nullable=True))
        batch.add_column(sa.Column("fix_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("seo_issues") as batch:
        batch.drop_column("fix_error")
        batch.drop_column("fix_diff")
        batch.drop_column("git_commit_hash")
        batch.drop_column("applied_by")
        batch.drop_column("fix_applied_at")

    with op.batch_alter_table("seo_projects") as batch:
        batch.drop_column("auto_fix_config")
        batch.drop_column("auto_fix_enabled")

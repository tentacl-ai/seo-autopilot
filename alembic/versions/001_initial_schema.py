"""Initial schema: SEO projects, audits, keywords, issues

Revision ID: 001
Revises:
Create Date: 2026-04-11

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create seo_projects table
    op.create_table(
        'seo_projects',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=True),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('adapter_type', sa.String(50), nullable=True),
        sa.Column('adapter_config', sa.JSON(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('schedule_cron', sa.String(100), nullable=True),
        sa.Column('enabled_sources', sa.JSON(), nullable=True),
        sa.Column('source_config', sa.JSON(), nullable=True),
        sa.Column('notify_channels', sa.JSON(), nullable=True),
        sa.Column('notify_config', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('seo_score', sa.Float(), nullable=True),
        sa.Column('content_score', sa.Float(), nullable=True),
        sa.Column('speed_score', sa.Float(), nullable=True),
        sa.Column('mobile_score', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('domain'),
    )
    op.create_index('ix_seo_projects_tenant_enabled', 'seo_projects', ['tenant_id', 'enabled'])

    # Create seo_audits table
    op.create_table(
        'seo_audits',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(64), nullable=False),
        sa.Column('tenant_id', sa.String(64), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('total_pages', sa.Integer(), nullable=True),
        sa.Column('total_keywords', sa.Integer(), nullable=True),
        sa.Column('issues_found', sa.Integer(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('analytics_data', sa.JSON(), nullable=True),
        sa.Column('gsc_clicks', sa.Integer(), nullable=True),
        sa.Column('gsc_impressions', sa.Integer(), nullable=True),
        sa.Column('gsc_ctr', sa.Float(), nullable=True),
        sa.Column('gsc_avg_position', sa.Float(), nullable=True),
        sa.Column('log_output', sa.Text(), nullable=True),
        sa.Column('errors', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['seo_projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_seo_audits_project_completed', 'seo_audits', ['project_id', 'completed_at'])

    # Create seo_keywords table
    op.create_table(
        'seo_keywords',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(64), nullable=False),
        sa.Column('audit_id', sa.String(64), nullable=True),
        sa.Column('tenant_id', sa.String(64), nullable=True),
        sa.Column('query', sa.String(255), nullable=False),
        sa.Column('search_volume', sa.Integer(), nullable=True),
        sa.Column('difficulty', sa.Float(), nullable=True),
        sa.Column('clicks', sa.Integer(), nullable=True),
        sa.Column('impressions', sa.Integer(), nullable=True),
        sa.Column('ctr', sa.Float(), nullable=True),
        sa.Column('position', sa.Float(), nullable=True),
        sa.Column('current_rank', sa.Integer(), nullable=True),
        sa.Column('target_rank', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('priority', sa.String(20), nullable=True),
        sa.Column('last_updated', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('best_page', sa.String(500), nullable=True),
        sa.Column('content_score', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['audit_id'], ['seo_audits.id'], ),
        sa.ForeignKeyConstraint(['project_id'], ['seo_projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_seo_keywords_project_query', 'seo_keywords', ['project_id', 'query'])
    op.create_index('ix_seo_keywords_priority', 'seo_keywords', ['project_id', 'priority'])

    # Create seo_issues table
    op.create_table(
        'seo_issues',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('project_id', sa.String(64), nullable=False),
        sa.Column('audit_id', sa.String(64), nullable=True),
        sa.Column('tenant_id', sa.String(64), nullable=True),
        sa.Column('category', sa.String(50), nullable=False),
        sa.Column('type', sa.String(100), nullable=True),
        sa.Column('severity', sa.String(20), nullable=True),
        sa.Column('priority', sa.String(20), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='open'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('affected_items', sa.JSON(), nullable=True),
        sa.Column('count', sa.Integer(), nullable=True),
        sa.Column('fix_suggestion', sa.Text(), nullable=True),
        sa.Column('estimated_impact', sa.String(255), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['audit_id'], ['seo_audits.id'], ),
        sa.ForeignKeyConstraint(['project_id'], ['seo_projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_seo_issues_project_status', 'seo_issues', ['project_id', 'status'])
    op.create_index('ix_seo_issues_priority', 'seo_issues', ['project_id', 'severity', 'priority'])


def downgrade() -> None:
    op.drop_index('ix_seo_issues_priority', table_name='seo_issues')
    op.drop_index('ix_seo_issues_project_status', table_name='seo_issues')
    op.drop_table('seo_issues')
    op.drop_index('ix_seo_keywords_priority', table_name='seo_keywords')
    op.drop_index('ix_seo_keywords_project_query', table_name='seo_keywords')
    op.drop_table('seo_keywords')
    op.drop_index('ix_seo_audits_project_completed', table_name='seo_audits')
    op.drop_table('seo_audits')
    op.drop_index('ix_seo_projects_tenant_enabled', table_name='seo_projects')
    op.drop_table('seo_projects')

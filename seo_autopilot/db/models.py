"""
SQLAlchemy Models for seo-autopilot

Multi-tenant SEO Database:
- SEOProject: Project configuration (mirrors ProjectConfig)
- SEOAudit: Audit run results
- SEOKeyword: Keywords from GSC/other sources
- SEOIssue: Auto-detected SEO issues
"""

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    DateTime,
    Boolean,
    Text,
    JSON,
    ForeignKey,
    Index,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

Base = declarative_base()


class SEOProject(Base):
    """Project/Domain in seo-autopilot"""

    __tablename__ = "seo_projects"

    id = Column(String(64), primary_key=True)  # project_id from ProjectConfig
    tenant_id = Column(String(64), index=True)  # Multi-tenant isolation
    domain = Column(String(255), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    adapter_type = Column(String(50))  # static, wordpress, fastapi, etc
    adapter_config = Column(JSON)
    enabled = Column(Boolean, default=True, index=True)
    schedule_cron = Column(String(100))
    enabled_sources = Column(JSON)  # ["gsc", "lighthouse", ...]
    source_config = Column(JSON)  # GSC property_url, credentials path, etc
    notify_channels = Column(JSON)  # ["telegram", "email", ...]
    notify_config = Column(JSON)

    # Tracking
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_run_at = Column(DateTime)
    next_run_at = Column(DateTime)

    # Metrics (cached from latest audit)
    seo_score = Column(Float)  # 0-100
    content_score = Column(Float)
    speed_score = Column(Float)
    mobile_score = Column(Float)

    # Relationships
    audits = relationship("SEOAudit", back_populates="project", cascade="all, delete-orphan")
    keywords = relationship("SEOKeyword", back_populates="project", cascade="all, delete-orphan")
    issues = relationship("SEOIssue", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_seo_projects_tenant_enabled", "tenant_id", "enabled"),)


class SEOAudit(Base):
    """Audit run for a project"""

    __tablename__ = "seo_audits"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(64), ForeignKey("seo_projects.id"), nullable=False, index=True)
    tenant_id = Column(String(64), index=True)

    # Run metadata
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    status = Column(String(50))  # running, completed, failed

    # Results
    total_pages = Column(Integer)
    total_keywords = Column(Integer)
    issues_found = Column(Integer)
    score = Column(Float)  # 0-100

    # Data snapshots
    analytics_data = Column(JSON)  # GSC SearchAnalytics snapshot
    gsc_clicks = Column(Integer)
    gsc_impressions = Column(Integer)
    gsc_ctr = Column(Float)
    gsc_avg_position = Column(Float)

    # Logs
    log_output = Column(Text)
    errors = Column(JSON)  # [{"stage": "analyzer", "error": "..."}]

    # Relationships
    project = relationship("SEOProject", back_populates="audits")
    keywords = relationship("SEOKeyword", back_populates="audit")
    issues = relationship("SEOIssue", back_populates="audit")

    __table_args__ = (Index("ix_seo_audits_project_completed", "project_id", "completed_at"),)


class SEOKeyword(Base):
    """Keyword from GSC or analysis"""

    __tablename__ = "seo_keywords"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(64), ForeignKey("seo_projects.id"), nullable=False, index=True)
    audit_id = Column(String(64), ForeignKey("seo_audits.id"), index=True)
    tenant_id = Column(String(64), index=True)

    # Keyword data
    query = Column(String(255), nullable=False)
    search_volume = Column(Integer)  # From Keyword Agent research
    difficulty = Column(Float)  # 0-100, from Keyword Agent

    # GSC metrics
    clicks = Column(Integer)
    impressions = Column(Integer)
    ctr = Column(Float)  # 0-1
    position = Column(Float)  # Average position in SERP

    # Rankings
    current_rank = Column(Integer)  # 1-100 in SERP
    target_rank = Column(Integer)

    # Status
    status = Column(String(50))  # active, declining, opportunity
    priority = Column(String(20))  # high, medium, low
    last_updated = Column(DateTime, default=datetime.utcnow)

    # Content mapping
    best_page = Column(String(500))  # URL ranking for this keyword
    content_score = Column(Float)  # 0-100

    # Relationships
    project = relationship("SEOProject", back_populates="keywords")
    audit = relationship("SEOAudit", back_populates="keywords")

    __table_args__ = (
        Index("ix_seo_keywords_project_query", "project_id", "query"),
        Index("ix_seo_keywords_priority", "project_id", "priority"),
    )


class SEOIssue(Base):
    """Auto-detected SEO issue"""

    __tablename__ = "seo_issues"

    id = Column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(64), ForeignKey("seo_projects.id"), nullable=False, index=True)
    audit_id = Column(String(64), ForeignKey("seo_audits.id"), index=True)
    tenant_id = Column(String(64), index=True)

    # Issue classification
    category = Column(String(50), nullable=False)  # meta, performance, mobile, security, etc
    type = Column(String(100))  # low_ctr_keywords, low_position, missing_meta, slow_core_web_vitals, etc
    severity = Column(String(20))  # critical, high, medium, low
    priority = Column(String(20))  # Must fix, Should fix, Nice to have
    status = Column(String(50), default="open")  # open, in_progress, fixed, wont_fix

    # Details
    title = Column(String(255), nullable=False)
    description = Column(Text)
    affected_items = Column(JSON)  # [{"url": "...", "detail": "..."}]
    count = Column(Integer)  # Number of affected pages/keywords

    # Auto-fix suggestion
    fix_suggestion = Column(Text)  # Generated by ContentAgent
    estimated_impact = Column(String(255))  # e.g., "Could improve CTR by 15%"

    # Tracking
    detected_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)

    # Relationships
    project = relationship("SEOProject", back_populates="issues")
    audit = relationship("SEOAudit", back_populates="issues")

    __table_args__ = (
        Index("ix_seo_issues_project_status", "project_id", "status"),
        Index("ix_seo_issues_priority", "project_id", "severity", "priority"),
    )

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class AuditorChecklistStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AuditorItemStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    EVIDENCE_SUBMITTED = "evidence_submitted"
    SATISFIED = "satisfied"
    NOT_APPLICABLE = "not_applicable"


class AuditorItemPriority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AuditorChecklist(Base):
    __tablename__ = "auditor_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    audit_type: Mapped[str | None] = mapped_column(String(120))
    audit_period_year: Mapped[str | None] = mapped_column(String(4))
    audit_date: Mapped[str | None] = mapped_column(String(10))
    auditor_name: Mapped[str | None] = mapped_column(String(120))
    framework: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    status: Mapped[AuditorChecklistStatus] = mapped_column(
        Enum(AuditorChecklistStatus), default=AuditorChecklistStatus.ACTIVE, nullable=False
    )
    source_import_id: Mapped[int | None] = mapped_column(ForeignKey("data_imports.id"))
    fields_found: Mapped[list[str] | None] = mapped_column(JSON)
    last_evidence_refresh: Mapped[str | None] = mapped_column(String(32))
    evidence_refresh_status: Mapped[str | None] = mapped_column(String(24))
    evidence_refresh_error: Mapped[str | None] = mapped_column(Text)


class AuditorChecklistItem(Base):
    __tablename__ = "auditor_checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("auditor_checklists.id"), nullable=False, index=True)
    source_import_id: Mapped[int | None] = mapped_column(ForeignKey("data_imports.id"), index=True)
    item_number: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    control_ids: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[AuditorItemStatus] = mapped_column(Enum(AuditorItemStatus), default=AuditorItemStatus.OPEN, nullable=False)
    our_response: Mapped[str | None] = mapped_column(Text)
    evidence_ids: Mapped[list[int] | None] = mapped_column(JSON)
    due_date: Mapped[str | None] = mapped_column(String(10))
    auditor_notes: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[AuditorItemPriority] = mapped_column(
        Enum(AuditorItemPriority), default=AuditorItemPriority.MEDIUM, nullable=False
    )
    raw_fields: Mapped[dict | None] = mapped_column(JSON)
    evidence_mapping: Mapped[dict | None] = mapped_column(JSON)

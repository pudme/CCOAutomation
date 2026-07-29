from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ClearanceLevel(str, enum.Enum):
    NONE = "none"
    PUBLIC_TRUST = "public_trust"
    SECRET = "secret"
    TOP_SECRET = "top_secret"
    TS_SCI = "ts_sci"


class AssignmentStatus(str, enum.Enum):
    PROPOSED = "proposed"
    COMMITTED = "committed"
    WON = "won"
    RELEASED = "released"


class GapStatus(str, enum.Enum):
    OPEN = "open"
    FILLED = "filled"
    AT_RISK = "at_risk"


class WorkforceStaff(Base):
    __tablename__ = "workforce_staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Optional cross-ref to a compliance personnel_records row when both exist.
    # Not a data source for staffing fields — roster is maintained via API/UI.
    personnel_id: Mapped[int | None] = mapped_column(
        ForeignKey("personnel_records.id"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(64))
    labor_category: Mapped[str | None] = mapped_column(String(128))
    clearance_level: Mapped[ClearanceLevel] = mapped_column(
        Enum(ClearanceLevel), default=ClearanceLevel.NONE, nullable=False
    )
    clearance_expiry: Mapped[str | None] = mapped_column(String(10))
    skills: Mapped[list[str] | None] = mapped_column(JSON)
    certifications: Mapped[list[str] | None] = mapped_column(JSON)
    location: Mapped[str | None] = mapped_column(String(128))
    employment_type: Mapped[str | None] = mapped_column(String(64))
    burdened_rate: Mapped[float | None] = mapped_column(Float)
    utilization_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    assignments: Mapped[list[WorkforceAssignment]] = relationship(
        back_populates="staff", cascade="all, delete-orphan"
    )


class WorkforcePursuit(Base):
    __tablename__ = "workforce_pursuits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Soft link to ARIA notice id — not a FK (ARIA is a separate app/DB).
    notice_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    agency: Mapped[str | None] = mapped_column(String(128))
    naics: Mapped[str | None] = mapped_column(String(16))
    set_aside: Mapped[str | None] = mapped_column(String(64))
    response_due: Mapped[str | None] = mapped_column(String(10))
    required_labor_categories: Mapped[list[str] | None] = mapped_column(JSON)
    required_clearance_level: Mapped[ClearanceLevel | None] = mapped_column(Enum(ClearanceLevel))
    key_personnel_slots: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    assignments: Mapped[list[WorkforceAssignment]] = relationship(
        back_populates="pursuit", cascade="all, delete-orphan"
    )
    gaps: Mapped[list[WorkforceGap]] = relationship(
        back_populates="pursuit", cascade="all, delete-orphan"
    )


class WorkforceAssignment(Base):
    __tablename__ = "workforce_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("workforce_staff.id"), nullable=False, index=True)
    pursuit_id: Mapped[int] = mapped_column(
        ForeignKey("workforce_pursuits.id"), nullable=False, index=True
    )
    role: Mapped[str | None] = mapped_column(String(128))
    commitment_pct: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus), default=AssignmentStatus.PROPOSED, nullable=False
    )

    staff: Mapped[WorkforceStaff] = relationship(back_populates="assignments")
    pursuit: Mapped[WorkforcePursuit] = relationship(back_populates="assignments")


class WorkforceGap(Base):
    __tablename__ = "workforce_gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pursuit_id: Mapped[int] = mapped_column(
        ForeignKey("workforce_pursuits.id"), nullable=False, index=True
    )
    labor_category: Mapped[str] = mapped_column(String(128), nullable=False)
    clearance_required: Mapped[ClearanceLevel | None] = mapped_column(Enum(ClearanceLevel))
    status: Mapped[GapStatus] = mapped_column(
        Enum(GapStatus), default=GapStatus.OPEN, nullable=False
    )
    flagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    pursuit: Mapped[WorkforcePursuit] = relationship(back_populates="gaps")

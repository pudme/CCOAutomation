from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class ControlStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    EVIDENCED = "evidenced"
    RISK_ACCEPTED = "risk_accepted"
    NOT_APPLICABLE = "not_applicable"


class EvidenceStatus(str, enum.Enum):
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    PENDING = "pending"


class FindingSeverity(str, enum.Enum):
    OBSERVATION = "observation"
    MINOR_NC = "minor_nc"
    MAJOR_NC = "major_nc"
    CRITICAL = "critical"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    VERIFIED = "verified"
    CLOSED = "closed"
    RISK_ACCEPTED = "risk_accepted"


class ObligationStatus(str, enum.Enum):
    CURRENT = "current"
    IN_PROGRESS = "in_progress"
    DUE_SOON = "due_soon"
    OVERDUE = "overdue"
    SATISFIED = "satisfied"
    WAIVED = "waived"


class EvidenceType(str, enum.Enum):
    POLICY = "policy"
    LOG = "log"
    REPORT = "report"
    SCREENSHOT = "screenshot"
    ATTESTATION = "attestation"
    RISK_ACCEPTANCE = "risk_acceptance"
    JUSTIFICATION = "justification"
    RECORD = "record"
    SCRIPT = "script"
    CONFIG = "config"
    CONTRACT = "contract"
    OTHER = "other"


class ImportStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


evidence_control_association = Table(
    "evidence_control",
    Base.metadata,
    Column("evidence_id", ForeignKey("evidence_items.id"), primary_key=True),
    Column("control_id", ForeignKey("controls.id"), primary_key=True),
)


finding_control_association = Table(
    "finding_control",
    Base.metadata,
    Column("finding_id", ForeignKey("findings.id"), primary_key=True),
    Column("control_id", ForeignKey("controls.id"), primary_key=True),
)


control_mappings_association = Table(
    "control_mappings",
    Base.metadata,
    Column("control_id", ForeignKey("controls.id"), primary_key=True),
    Column("mapped_control_id", ForeignKey("controls.id"), primary_key=True),
)


class Framework(Base):
    __tablename__ = "frameworks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    short_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    loaded_date: Mapped[str | None] = mapped_column(String(10))

    controls: Mapped[list[Control]] = relationship(back_populates="framework", cascade="all, delete-orphan")


class Control(Base):
    __tablename__ = "controls"
    __table_args__ = (UniqueConstraint("framework_id", "control_id", name="uq_framework_control"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    framework_id: Mapped[int] = mapped_column(ForeignKey("frameworks.id"), nullable=False, index=True)
    control_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    implementation_guidance: Mapped[str | None] = mapped_column(Text)
    owner_default: Mapped[str | None] = mapped_column(String(120))
    review_cadence_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    status: Mapped[ControlStatus] = mapped_column(Enum(ControlStatus), default=ControlStatus.NOT_STARTED, nullable=False)
    status_notes: Mapped[str | None] = mapped_column(Text)
    last_reviewed: Mapped[str | None] = mapped_column(String(10))
    maps_to: Mapped[dict[str, list[str]] | None] = mapped_column(JSON)

    framework: Mapped[Framework] = relationship(back_populates="controls")
    evidence_requirements: Mapped[list[EvidenceRequirement]] = relationship(
        back_populates="control", cascade="all, delete-orphan"
    )
    evidence_items: Mapped[list[EvidenceItem]] = relationship(
        secondary=evidence_control_association, back_populates="controls"
    )
    findings: Mapped[list[Finding]] = relationship(
        secondary=finding_control_association, back_populates="controls"
    )
    mapped_to: Mapped[list[Control]] = relationship(
        "Control",
        secondary=control_mappings_association,
        primaryjoin=id == control_mappings_association.c.control_id,
        secondaryjoin=id == control_mappings_association.c.mapped_control_id,
    )


class EvidenceRequirement(Base):
    __tablename__ = "evidence_requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), nullable=False, index=True)
    evidence_type: Mapped[EvidenceType] = mapped_column(Enum(EvidenceType), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    control: Mapped[Control] = relationship(back_populates="evidence_requirements")


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500))
    evidence_type: Mapped[EvidenceType] = mapped_column(Enum(EvidenceType), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    entity: Mapped[str | None] = mapped_column(String(64))
    collected_date: Mapped[str | None] = mapped_column(String(10))
    review_date: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[EvidenceStatus] = mapped_column(Enum(EvidenceStatus), default=EvidenceStatus.CURRENT, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    analysis_confidence: Mapped[str | None] = mapped_column(String(16))
    analysis_summary: Mapped[str | None] = mapped_column(Text)
    library: Mapped[str] = mapped_column(String(16), default="main", nullable=False)

    controls: Mapped[list[Control]] = relationship(secondary=evidence_control_association, back_populates="evidence_items")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    framework_id: Mapped[int] = mapped_column(ForeignKey("frameworks.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[FindingSeverity] = mapped_column(Enum(FindingSeverity), nullable=False)
    status: Mapped[FindingStatus] = mapped_column(Enum(FindingStatus), default=FindingStatus.OPEN, nullable=False)
    discovered_date: Mapped[str | None] = mapped_column(String(10))
    target_close: Mapped[str | None] = mapped_column(String(10))
    closed_date: Mapped[str | None] = mapped_column(String(10))
    owner: Mapped[str | None] = mapped_column(String(120))

    framework: Mapped[Framework] = relationship()
    controls: Mapped[list[Control]] = relationship(secondary=finding_control_association, back_populates="findings")
    corrective_actions: Mapped[list[CorrectiveAction]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )


class CorrectiveAction(Base):
    __tablename__ = "corrective_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(10))
    completed_date: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[FindingStatus] = mapped_column(Enum(FindingStatus), default=FindingStatus.OPEN, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    finding: Mapped[Finding] = relationship(back_populates="corrective_actions")


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obligation_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(String(120))
    due_date: Mapped[str | None] = mapped_column(String(10))
    cadence: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[ObligationStatus] = mapped_column(Enum(ObligationStatus), default=ObligationStatus.CURRENT, nullable=False)
    last_satisfied: Mapped[str | None] = mapped_column(String(10))
    notes: Mapped[str | None] = mapped_column(Text)


class PersonnelRecord(Base):
    __tablename__ = "personnel_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[str | None] = mapped_column(String(50))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200))
    entra_upn: Mapped[str | None] = mapped_column(String(200))
    entity: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    termination_date: Mapped[str | None] = mapped_column(String(10))
    entra_account_active: Mapped[bool | None] = mapped_column(Boolean)
    mfa_configured: Mapped[bool | None] = mapped_column(Boolean)
    training_complete: Mapped[bool | None] = mapped_column(Boolean)
    training_date: Mapped[str | None] = mapped_column(String(10))
    nda_on_file: Mapped[bool | None] = mapped_column(Boolean)
    nda_date: Mapped[str | None] = mapped_column(String(10))
    background_check: Mapped[bool | None] = mapped_column(Boolean)
    last_synced: Mapped[str | None] = mapped_column(String(10))
    flags: Mapped[list[str] | None] = mapped_column(JSON)


class AgentActionLog(Base):
    __tablename__ = "agent_action_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"))
    operator: Mapped[str] = mapped_column(String(100), default="Michael DuPlantis", nullable=False)

    conversation: Mapped[Conversation | None] = relationship(back_populates="actions")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))
    operator: Mapped[str] = mapped_column(String(100), default="Michael DuPlantis", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan")
    actions: Mapped[list[AgentActionLog]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class DataImport(Base):
    __tablename__ = "data_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    source_system: Mapped[str] = mapped_column(String(120), nullable=False)
    data_date: Mapped[str] = mapped_column(String(10), nullable=False)
    framework: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[str | None] = mapped_column(String(64), index=True)
    file_size: Mapped[int | None] = mapped_column(Integer)
    control_ids: Mapped[list[str] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    auditor_engagement_name: Mapped[str | None] = mapped_column(String(255))
    auditor_engagement_type: Mapped[str | None] = mapped_column(String(120))
    auditor_certification_body: Mapped[str | None] = mapped_column(String(120))
    auditor_period_year: Mapped[str | None] = mapped_column(String(4))
    auditor_merge_with_existing: Mapped[bool | None] = mapped_column(Boolean)
    minio_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ImportStatus] = mapped_column(Enum(ImportStatus), default=ImportStatus.QUEUED, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    library: Mapped[str] = mapped_column(String(16), default="main", nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    duplicate_status: Mapped[str] = mapped_column(String(32), default="unique", nullable=False)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("data_imports.id"))
    duplicate_confidence: Mapped[str | None] = mapped_column(String(16))
    duplicate_reason: Mapped[str | None] = mapped_column(Text)
    duplicate_flag_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    identified_summary: Mapped[str | None] = mapped_column(Text)
    proposed_updates: Mapped[list[str] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class ChangeLog(Base):
    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)


class BatchImport(Base):
    __tablename__ = "batch_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    total_files: Mapped[int] = mapped_column(Integer, nullable=False)
    operator: Mapped[str] = mapped_column(String(100), default="Michael DuPlantis", nullable=False)
    skipped_files: Mapped[list[dict[str, str]] | None] = mapped_column(JSON)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


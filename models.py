"""
core/models.py — Compliance Platform Database Schema
SQLAlchemy 2.x ORM models. This is the single source of truth for all data structures.
Never hardcode framework-specific logic here. Frameworks are loaded from YAML config files.
"""

from __future__ import annotations
from datetime import date
from typing import List, Optional
from sqlalchemy import (
    String, Text, Boolean, Integer, Float, Date, ForeignKey,
    Table, Column, Enum as SAEnum, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ControlStatus(str, enum.Enum):
    NOT_STARTED   = "not_started"
    IN_PROGRESS   = "in_progress"
    EVIDENCED     = "evidenced"
    RISK_ACCEPTED = "risk_accepted"
    NOT_APPLICABLE = "not_applicable"


class EvidenceStatus(str, enum.Enum):
    CURRENT  = "current"
    STALE    = "stale"       # exists but past review cadence
    MISSING  = "missing"
    PENDING  = "pending"     # expected but not yet received


class FindingSeverity(str, enum.Enum):
    OBSERVATION  = "observation"
    MINOR_NC     = "minor_nc"
    MAJOR_NC     = "major_nc"
    CRITICAL     = "critical"


class FindingStatus(str, enum.Enum):
    OPEN         = "open"
    IN_PROGRESS  = "in_progress"
    RESOLVED     = "resolved"
    VERIFIED     = "verified"
    CLOSED       = "closed"
    RISK_ACCEPTED = "risk_accepted"


class ObligationStatus(str, enum.Enum):
    CURRENT     = "current"
    DUE_SOON    = "due_soon"    # within 30 days
    OVERDUE     = "overdue"
    SATISFIED   = "satisfied"
    WAIVED      = "waived"


class EvidenceType(str, enum.Enum):
    POLICY         = "policy"
    LOG            = "log"
    REPORT         = "report"
    SCREENSHOT     = "screenshot"
    ATTESTATION    = "attestation"
    RISK_ACCEPTANCE = "risk_acceptance"
    JUSTIFICATION  = "justification"
    RECORD         = "record"
    SCRIPT         = "script"
    CONFIG         = "config"
    CONTRACT       = "contract"
    OTHER          = "other"


# ---------------------------------------------------------------------------
# Association Tables (many-to-many)
# ---------------------------------------------------------------------------

# Evidence item satisfies one or more controls (across frameworks)
evidence_control_association = Table(
    "evidence_control",
    Base.metadata,
    Column("evidence_id", Integer, ForeignKey("evidence_items.id"), primary_key=True),
    Column("control_id",  Integer, ForeignKey("controls.id"),       primary_key=True),
)

# Control maps to equivalent controls in other frameworks
control_mapping_table = Table(
    "control_mappings",
    Base.metadata,
    Column("control_id",        Integer, ForeignKey("controls.id"), primary_key=True),
    Column("mapped_control_id", Integer, ForeignKey("controls.id"), primary_key=True),
)

# Finding linked to one or more controls
finding_control_association = Table(
    "finding_control",
    Base.metadata,
    Column("finding_id", Integer, ForeignKey("findings.id"), primary_key=True),
    Column("control_id", Integer, ForeignKey("controls.id"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Framework
# ---------------------------------------------------------------------------

class Framework(Base):
    """
    A compliance framework loaded from a YAML config file.
    Examples: ISO 27001:2022, CMMC Level 2, ISO 9001:2015
    """
    __tablename__ = "frameworks"

    id:              Mapped[int]           = mapped_column(primary_key=True)
    name:            Mapped[str]           = mapped_column(String(100), unique=True, nullable=False)
    version:         Mapped[str]           = mapped_column(String(20), nullable=False)
    short_name:      Mapped[str]           = mapped_column(String(30), nullable=False)  # e.g. "iso27001"
    description:     Mapped[Optional[str]] = mapped_column(Text)
    certification_body: Mapped[Optional[str]] = mapped_column(String(100))
    active:          Mapped[bool]          = mapped_column(Boolean, default=True)
    loaded_date:     Mapped[Optional[str]] = mapped_column(String(10))  # YYYY-MM-DD

    controls:        Mapped[List["Control"]] = relationship("Control", back_populates="framework")

    def __repr__(self) -> str:
        return f"<Framework {self.short_name} {self.version}>"


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------

class Control(Base):
    """
    A single control or requirement from a compliance framework.
    Never hardcode framework-specific content here — load from YAML.
    """
    __tablename__ = "controls"
    __table_args__ = (
        UniqueConstraint("framework_id", "control_id", name="uq_framework_control"),
    )

    id:              Mapped[int]           = mapped_column(primary_key=True)
    framework_id:    Mapped[int]           = mapped_column(ForeignKey("frameworks.id"), nullable=False)
    control_id:      Mapped[str]           = mapped_column(String(30), nullable=False)   # e.g. "A.5.1"
    title:           Mapped[str]           = mapped_column(String(200), nullable=False)
    domain:          Mapped[str]           = mapped_column(String(100), nullable=False)  # e.g. "Organizational Controls"
    description:     Mapped[Optional[str]] = mapped_column(Text)
    owner_default:   Mapped[Optional[str]] = mapped_column(String(100))  # role title, not name
    review_cadence_days: Mapped[int]       = mapped_column(Integer, default=365)
    status:          Mapped[str]           = mapped_column(
                         SAEnum(ControlStatus), default=ControlStatus.NOT_STARTED
                     )
    status_notes:    Mapped[Optional[str]] = mapped_column(Text)
    last_reviewed:   Mapped[Optional[str]] = mapped_column(String(10))   # YYYY-MM-DD
    soa_included:    Mapped[bool]          = mapped_column(Boolean, default=True)
    soa_justification: Mapped[Optional[str]] = mapped_column(Text)

    framework:       Mapped["Framework"]          = relationship("Framework", back_populates="controls")
    evidence_items:  Mapped[List["EvidenceItem"]] = relationship(
                         "EvidenceItem",
                         secondary=evidence_control_association,
                         back_populates="controls"
                     )
    evidence_requirements: Mapped[List["EvidenceRequirement"]] = relationship(
                         "EvidenceRequirement", back_populates="control", cascade="all, delete-orphan"
                     )
    findings:        Mapped[List["Finding"]] = relationship(
                         "Finding",
                         secondary=finding_control_association,
                         back_populates="controls"
                     )
    mapped_to:       Mapped[List["Control"]] = relationship(
                         "Control",
                         secondary=control_mapping_table,
                         primaryjoin=id == control_mapping_table.c.control_id,
                         secondaryjoin=id == control_mapping_table.c.mapped_control_id,
                     )

    def __repr__(self) -> str:
        return f"<Control {self.control_id} [{self.framework.short_name}]>"

    @property
    def is_evidenced(self) -> bool:
        return any(
            e.status == EvidenceStatus.CURRENT for e in self.evidence_items
        )

    @property
    def gap_status(self) -> EvidenceStatus:
        if self.status in (ControlStatus.RISK_ACCEPTED, ControlStatus.NOT_APPLICABLE):
            return EvidenceStatus.CURRENT
        current = [e for e in self.evidence_items if e.status == EvidenceStatus.CURRENT]
        if not current:
            return EvidenceStatus.MISSING
        required_types = {r.evidence_type for r in self.evidence_requirements}
        provided_types = {e.evidence_type for e in current}
        if required_types and not required_types.issubset(provided_types):
            return EvidenceStatus.PENDING
        return EvidenceStatus.CURRENT


# ---------------------------------------------------------------------------
# Evidence Requirement (what a control expects)
# ---------------------------------------------------------------------------

class EvidenceRequirement(Base):
    """
    Specifies what type(s) of evidence a control requires.
    Loaded from the framework YAML. One control can have multiple requirements.
    """
    __tablename__ = "evidence_requirements"

    id:            Mapped[int] = mapped_column(primary_key=True)
    control_id:    Mapped[int] = mapped_column(ForeignKey("controls.id"), nullable=False)
    evidence_type: Mapped[str] = mapped_column(SAEnum(EvidenceType), nullable=False)
    description:   Mapped[Optional[str]] = mapped_column(String(200))
    required:      Mapped[bool] = mapped_column(Boolean, default=True)

    control: Mapped["Control"] = relationship("Control", back_populates="evidence_requirements")


# ---------------------------------------------------------------------------
# Evidence Item (what actually exists)
# ---------------------------------------------------------------------------

class EvidenceItem(Base):
    """
    A piece of evidence on file. Can satisfy controls across multiple frameworks.
    File path is relative to the evidence_root defined in settings.yaml.
    """
    __tablename__ = "evidence_items"

    id:            Mapped[int]           = mapped_column(primary_key=True)
    filename:      Mapped[str]           = mapped_column(String(300), nullable=False)
    file_path:     Mapped[Optional[str]] = mapped_column(String(500))  # relative to evidence_root
    evidence_type: Mapped[str]           = mapped_column(SAEnum(EvidenceType), nullable=False)
    description:   Mapped[Optional[str]] = mapped_column(Text)
    entity:        Mapped[Optional[str]] = mapped_column(String(50))   # "Apprio", "Canaide", or None (both)
    collected_date: Mapped[Optional[str]] = mapped_column(String(10))  # YYYY-MM-DD
    review_date:   Mapped[Optional[str]] = mapped_column(String(10))   # when it expires / needs refresh
    status:        Mapped[str]           = mapped_column(
                       SAEnum(EvidenceStatus), default=EvidenceStatus.CURRENT
                   )
    notes:         Mapped[Optional[str]] = mapped_column(Text)

    controls: Mapped[List["Control"]] = relationship(
        "Control",
        secondary=evidence_control_association,
        back_populates="evidence_items"
    )

    def __repr__(self) -> str:
        return f"<EvidenceItem {self.filename} [{self.status}]>"


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------

class Finding(Base):
    """
    An audit finding. May relate to one or more controls across one or more frameworks.
    Lifecycle: Open → In Progress → Resolved → Verified → Closed
    """
    __tablename__ = "findings"

    id:              Mapped[int]           = mapped_column(primary_key=True)
    finding_id:      Mapped[str]           = mapped_column(String(20), unique=True, nullable=False)  # e.g. AF-05, PF-01
    framework_id:    Mapped[int]           = mapped_column(ForeignKey("frameworks.id"), nullable=False)
    audit_cycle:     Mapped[Optional[str]] = mapped_column(String(20))   # e.g. "2025-2026"
    title:           Mapped[str]           = mapped_column(String(300), nullable=False)
    description:     Mapped[Optional[str]] = mapped_column(Text)
    severity:        Mapped[str]           = mapped_column(SAEnum(FindingSeverity), nullable=False)
    status:          Mapped[str]           = mapped_column(SAEnum(FindingStatus), default=FindingStatus.OPEN)
    discovered_date: Mapped[Optional[str]] = mapped_column(String(10))
    target_close:    Mapped[Optional[str]] = mapped_column(String(10))
    closed_date:     Mapped[Optional[str]] = mapped_column(String(10))
    root_cause:      Mapped[Optional[str]] = mapped_column(Text)
    owner:           Mapped[Optional[str]] = mapped_column(String(100))  # role title

    controls:            Mapped[List["Control"]]           = relationship(
                             "Control",
                             secondary=finding_control_association,
                             back_populates="findings"
                         )
    corrective_actions:  Mapped[List["CorrectiveAction"]]  = relationship(
                             "CorrectiveAction", back_populates="finding", cascade="all, delete-orphan"
                         )
    framework:           Mapped["Framework"] = relationship("Framework")

    def __repr__(self) -> str:
        return f"<Finding {self.finding_id} [{self.severity} / {self.status}]>"


# ---------------------------------------------------------------------------
# Corrective Action
# ---------------------------------------------------------------------------

class CorrectiveAction(Base):
    """
    A corrective action tied to a finding. A finding can have multiple CAs.
    """
    __tablename__ = "corrective_actions"

    id:           Mapped[int]           = mapped_column(primary_key=True)
    finding_id:   Mapped[int]           = mapped_column(ForeignKey("findings.id"), nullable=False)
    description:  Mapped[str]           = mapped_column(Text, nullable=False)
    owner:        Mapped[Optional[str]] = mapped_column(String(100))
    due_date:     Mapped[Optional[str]] = mapped_column(String(10))
    completed_date: Mapped[Optional[str]] = mapped_column(String(10))
    status:       Mapped[str]           = mapped_column(SAEnum(FindingStatus), default=FindingStatus.OPEN)
    notes:        Mapped[Optional[str]] = mapped_column(Text)
    evidence_id:  Mapped[Optional[int]] = mapped_column(ForeignKey("evidence_items.id"))

    finding:  Mapped["Finding"]            = relationship("Finding", back_populates="corrective_actions")
    evidence: Mapped[Optional["EvidenceItem"]] = relationship("EvidenceItem")

    def __repr__(self) -> str:
        return f"<CorrectiveAction [Finding {self.finding_id}] [{self.status}]>"


# ---------------------------------------------------------------------------
# Obligation
# ---------------------------------------------------------------------------

class Obligation(Base):
    """
    An external compliance obligation not tied to a specific framework control.
    Covers: probationary conditions, BOP contract requirements, certification
    body conditions, legal/regulatory mandates.
    """
    __tablename__ = "obligations"

    id:           Mapped[int]           = mapped_column(primary_key=True)
    obligation_id: Mapped[str]          = mapped_column(String(20), unique=True)  # e.g. OBL-001
    source:       Mapped[str]           = mapped_column(String(100), nullable=False)  # e.g. "BOP Contract", "Probationary"
    description:  Mapped[str]           = mapped_column(Text, nullable=False)
    owner:        Mapped[Optional[str]] = mapped_column(String(100))
    due_date:     Mapped[Optional[str]] = mapped_column(String(10))   # YYYY-MM-DD or "Rolling"
    cadence:      Mapped[Optional[str]] = mapped_column(String(50))   # e.g. "Monthly", "Annual", "One-time"
    status:       Mapped[str]           = mapped_column(SAEnum(ObligationStatus), default=ObligationStatus.CURRENT)
    last_satisfied: Mapped[Optional[str]] = mapped_column(String(10))
    notes:        Mapped[Optional[str]] = mapped_column(Text)
    evidence_id:  Mapped[Optional[int]] = mapped_column(ForeignKey("evidence_items.id"))

    evidence: Mapped[Optional["EvidenceItem"]] = relationship("EvidenceItem")

    def __repr__(self) -> str:
        return f"<Obligation {self.obligation_id} [{self.source}] [{self.status}]>"


# ---------------------------------------------------------------------------
# Personnel Record (compliance-scoped — not a full HR system)
# ---------------------------------------------------------------------------

class PersonnelRecord(Base):
    """
    Compliance-relevant personnel data, populated from HR exports and Entra ID.
    This is NOT a full HR system. It tracks only what's needed for control evidence.
    Refreshed from source data on each personnel_checker run.
    """
    __tablename__ = "personnel_records"

    id:                  Mapped[int]           = mapped_column(primary_key=True)
    employee_id:         Mapped[Optional[str]] = mapped_column(String(50))
    display_name:        Mapped[str]           = mapped_column(String(200), nullable=False)
    email:               Mapped[Optional[str]] = mapped_column(String(200))
    entra_upn:           Mapped[Optional[str]] = mapped_column(String(200))
    entity:              Mapped[Optional[str]] = mapped_column(String(50))   # "Apprio" or "Canaide"
    active:              Mapped[bool]          = mapped_column(Boolean, default=True)
    termination_date:    Mapped[Optional[str]] = mapped_column(String(10))
    entra_account_active: Mapped[Optional[bool]] = mapped_column(Boolean)
    mfa_configured:      Mapped[Optional[bool]] = mapped_column(Boolean)
    training_complete:   Mapped[Optional[bool]] = mapped_column(Boolean)
    training_date:       Mapped[Optional[str]] = mapped_column(String(10))
    nda_on_file:         Mapped[Optional[bool]] = mapped_column(Boolean)
    nda_date:            Mapped[Optional[str]] = mapped_column(String(10))
    background_check:    Mapped[Optional[bool]] = mapped_column(Boolean)  # CMMC PS.L2-3.9.1
    last_synced:         Mapped[Optional[str]] = mapped_column(String(10))
    flags:               Mapped[Optional[str]] = mapped_column(Text)  # JSON list of compliance flags

    def __repr__(self) -> str:
        return f"<PersonnelRecord {self.display_name} [active={self.active}]>"


# ---------------------------------------------------------------------------
# Audit Run Log
# ---------------------------------------------------------------------------

class AuditRun(Base):
    """
    Records each time a scan or check is executed. Provides audit trail for the platform itself.
    """
    __tablename__ = "audit_runs"

    id:         Mapped[int]           = mapped_column(primary_key=True)
    run_type:   Mapped[str]           = mapped_column(String(50))   # "gap_scan", "personnel_check", etc.
    run_date:   Mapped[str]           = mapped_column(String(10))
    framework:  Mapped[Optional[str]] = mapped_column(String(50))
    result_summary: Mapped[Optional[str]] = mapped_column(Text)     # JSON summary
    output_path: Mapped[Optional[str]] = mapped_column(String(500))
    operator:   Mapped[str]           = mapped_column(String(100), default="Michael DuPlantis")

    def __repr__(self) -> str:
        return f"<AuditRun {self.run_type} {self.run_date}>"

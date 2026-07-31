from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from framework_constants import CMMC_FRAMEWORKS
from models.compliance import Framework, PersonnelRecord


class TrainingGapItem(BaseModel):
    employee_name: str
    email: str | None
    entity: str | None
    flag_type: str


class AccessRevocationItem(BaseModel):
    employee_name: str
    email: str | None
    termination_date: str | None


class EntityGapItem(BaseModel):
    employee_name: str
    email: str | None
    entity: str | None


class CmmcGapItem(BaseModel):
    employee_name: str
    email: str | None
    check_name: str


class PersonnelSummary(BaseModel):
    training_gap_count: int
    access_revocation_count: int
    nda_gap_count: int
    mfa_gap_count: int
    cmmc_gap_count: int
    total_exceptions: int


class PersonnelComplianceReport(BaseModel):
    run_timestamp: str
    total_active_employees: int
    training_gaps: list[TrainingGapItem]
    access_revocation_exceptions: list[AccessRevocationItem]
    nda_gaps: list[EntityGapItem]
    mfa_gaps: list[EntityGapItem]
    cmmc_gaps: list[CmmcGapItem]
    summary: PersonnelSummary


def _name_match_flag(record: PersonnelRecord) -> str:
    display_name = (record.display_name or "").strip()
    if not display_name:
        return "manual_review_unresolved_name"
    parts = [p for p in display_name.split(" ") if p]
    if len(parts) == 1:
        return "last_name_only_match_manual_verification"
    if len(parts[0]) == 1:
        return "first_initial_last_name_match"
    return "exact_name_match"


def _coalesce_personnel(target: PersonnelRecord, source: PersonnelRecord) -> None:
    str_fields = [
        "display_name",
        "email",
        "employee_id",
        "entity",
        "termination_date",
        "training_date",
        "nda_date",
        "last_synced",
    ]
    bool_fields = [
        "active",
        "entra_account_active",
        "mfa_configured",
        "training_complete",
        "nda_on_file",
        "background_check",
    ]
    for field in str_fields:
        if getattr(target, field) in (None, "") and getattr(source, field) not in (None, ""):
            setattr(target, field, getattr(source, field))
    for field in bool_fields:
        if getattr(target, field) is None and getattr(source, field) is not None:
            setattr(target, field, getattr(source, field))
    source_flags = source.flags or []
    if source_flags:
        target_flags = target.flags or []
        target.flags = sorted({*target_flags, *source_flags})


async def _dedupe_personnel_by_entra_upn(session: AsyncSession) -> None:
    records = list(
        (
            await session.execute(
                select(PersonnelRecord).order_by(
                    PersonnelRecord.last_synced.desc().nullslast(),
                    PersonnelRecord.id.desc(),
                )
            )
        ).scalars()
    )
    by_identity: dict[str, PersonnelRecord] = {}
    for record in records:
        upn = (record.entra_upn or "").strip().lower()
        email = (record.email or "").strip().lower()
        employee_id = (record.employee_id or "").strip().lower()
        display_name = (record.display_name or "").strip().lower()
        key = ""
        if upn:
            key = f"upn:{upn}"
        elif email:
            key = f"email:{email}"
        elif employee_id:
            key = f"employee:{employee_id}"
        elif display_name:
            key = f"name:{display_name}"
        if not key:
            continue
        keeper = by_identity.get(key)
        if keeper is None:
            by_identity[key] = record
            continue
        _coalesce_personnel(keeper, record)
        await session.delete(record)
    await session.commit()


async def run_personnel_check(
    session: AsyncSession,
    *,
    include_canaide: bool = False,
) -> PersonnelComplianceReport:
    await _dedupe_personnel_by_entra_upn(session)
    active_stmt = select(PersonnelRecord).where(PersonnelRecord.active.is_(True))
    terminated_stmt = select(PersonnelRecord).where(PersonnelRecord.active.is_(False))
    if not include_canaide:
        active_stmt = active_stmt.where(PersonnelRecord.entity == "Apprio")
        terminated_stmt = terminated_stmt.where(PersonnelRecord.entity == "Apprio")
    active_result = await session.execute(active_stmt)
    active_records = list(active_result.scalars())

    terminated_result = await session.execute(terminated_stmt)
    terminated_records = list(terminated_result.scalars())

    training_gaps = [
        TrainingGapItem(
            employee_name=record.display_name,
            email=record.email,
            entity=record.entity,
            flag_type=_name_match_flag(record) if record.training_complete in (False, None) else "exact_name_match",
        )
        for record in active_records
        if record.training_complete is False
    ]

    access_revocation_exceptions = [
        AccessRevocationItem(
            employee_name=record.display_name,
            email=record.email,
            termination_date=record.termination_date,
        )
        for record in terminated_records
        if record.entra_account_active is True
    ]

    nda_gaps = [
        EntityGapItem(employee_name=record.display_name, email=record.email, entity=record.entity)
        for record in active_records
        if record.nda_on_file is False
    ]

    mfa_gaps = [
        EntityGapItem(employee_name=record.display_name, email=record.email, entity=record.entity)
        for record in active_records
        if record.mfa_configured is False
    ]

    framework_count_result = await session.execute(
        select(func.count(Framework.id)).where(Framework.short_name.in_(CMMC_FRAMEWORKS))
    )
    cmmc_enabled = (framework_count_result.scalar_one() or 0) > 0
    cmmc_gaps: list[CmmcGapItem] = []
    if cmmc_enabled:
        for record in active_records:
            if record.background_check in (False, None):
                cmmc_gaps.append(
                    CmmcGapItem(
                        employee_name=record.display_name,
                        email=record.email,
                        check_name="PS.L2-3.9.1 background_check_missing",
                    )
                )
            if record.training_complete is False:
                cmmc_gaps.append(
                    CmmcGapItem(
                        employee_name=record.display_name,
                        email=record.email,
                        check_name="AT.L2-3.2.1 training_gap",
                    )
                )

    summary = PersonnelSummary(
        training_gap_count=len(training_gaps),
        access_revocation_count=len(access_revocation_exceptions),
        nda_gap_count=len(nda_gaps),
        mfa_gap_count=len(mfa_gaps),
        cmmc_gap_count=len(cmmc_gaps),
        total_exceptions=(
            len(training_gaps)
            + len(access_revocation_exceptions)
            + len(nda_gaps)
            + len(mfa_gaps)
            + len(cmmc_gaps)
        ),
    )

    return PersonnelComplianceReport(
        run_timestamp=datetime.now(timezone.utc).isoformat(),
        total_active_employees=len(active_records),
        training_gaps=training_gaps,
        access_revocation_exceptions=access_revocation_exceptions,
        nda_gaps=nda_gaps,
        mfa_gaps=mfa_gaps,
        cmmc_gaps=cmmc_gaps,
        summary=summary,
    )

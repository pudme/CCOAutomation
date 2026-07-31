from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.workforce import (
    AssignmentStatus,
    ClearanceLevel,
    GapStatus,
    WorkforceAssignment,
    WorkforceGap,
    WorkforcePursuit,
    WorkforceStaff,
)
from services.change_log import log_change
from services.workforce_alignment import analyze_pursuit_gaps, check_overcommitment

router = APIRouter(prefix="/workforce", tags=["workforce"])


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


def _serialize_staff(staff: WorkforceStaff) -> dict:
    return {
        "id": staff.id,
        "personnel_id": staff.personnel_id,
        "display_name": staff.display_name,
        "entity": staff.entity,
        "labor_category": staff.labor_category,
        "clearance_level": staff.clearance_level.value if staff.clearance_level else None,
        "clearance_expiry": staff.clearance_expiry,
        "skills": staff.skills or [],
        "certifications": staff.certifications or [],
        "location": staff.location,
        "employment_type": staff.employment_type,
        "burdened_rate": staff.burdened_rate,
        "utilization_pct": staff.utilization_pct,
        "updated_at": staff.updated_at.isoformat() if staff.updated_at else None,
    }


def _serialize_pursuit(pursuit: WorkforcePursuit) -> dict:
    return {
        "id": pursuit.id,
        "notice_id": pursuit.notice_id,
        "title": pursuit.title,
        "agency": pursuit.agency,
        "naics": pursuit.naics,
        "set_aside": pursuit.set_aside,
        "response_due": pursuit.response_due,
        "required_labor_categories": pursuit.required_labor_categories or [],
        "required_clearance_level": (
            pursuit.required_clearance_level.value if pursuit.required_clearance_level else None
        ),
        "key_personnel_slots": pursuit.key_personnel_slots,
        "source": pursuit.source,
        "imported_at": pursuit.imported_at.isoformat() if pursuit.imported_at else None,
    }


def _serialize_assignment(assignment: WorkforceAssignment) -> dict:
    return {
        "id": assignment.id,
        "staff_id": assignment.staff_id,
        "pursuit_id": assignment.pursuit_id,
        "role": assignment.role,
        "commitment_pct": assignment.commitment_pct,
        "status": assignment.status.value if assignment.status else None,
    }


def _serialize_gap(gap: WorkforceGap) -> dict:
    return {
        "id": gap.id,
        "pursuit_id": gap.pursuit_id,
        "labor_category": gap.labor_category,
        "clearance_required": gap.clearance_required.value if gap.clearance_required else None,
        "status": gap.status.value if gap.status else None,
        "flagged_at": gap.flagged_at.isoformat() if gap.flagged_at else None,
        "resolved_at": gap.resolved_at.isoformat() if gap.resolved_at else None,
        "notes": gap.notes,
    }


# ---------------------------------------------------------------------------
# Staff CRUD
# ---------------------------------------------------------------------------


class StaffCreateRequest(BaseModel):
    display_name: str
    personnel_id: int | None = None
    entity: str | None = None
    labor_category: str | None = None
    clearance_level: str = "none"
    clearance_expiry: str | None = None
    skills: list[str] | None = None
    certifications: list[str] | None = None
    location: str | None = None
    employment_type: str | None = None
    burdened_rate: float | None = None
    utilization_pct: float = 0.0


class StaffPatchRequest(BaseModel):
    display_name: str | None = None
    personnel_id: int | None = None
    entity: str | None = None
    labor_category: str | None = None
    clearance_level: str | None = None
    clearance_expiry: str | None = None
    skills: list[str] | None = None
    certifications: list[str] | None = None
    location: str | None = None
    employment_type: str | None = None
    burdened_rate: float | None = None
    utilization_pct: float | None = None


@router.get("/staff")
async def list_staff(
    include_canaide: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(WorkforceStaff).order_by(WorkforceStaff.id.asc())
    if not include_canaide:
        stmt = stmt.where(WorkforceStaff.entity == "Apprio")
    rows = list((await session.execute(stmt)).scalars())
    return [_serialize_staff(row) for row in rows]


# NOTE: POST /workforce/staff/import-from-personnel was removed (deprecated).
# personnel_records is compliance-evidence data only (MFA, training, NDA, background
# check, Entra sync). It holds no staffing-relevant fields (labor_category,
# clearance_level, utilization_pct, skills, etc.) and never will — CCOA has no HR
# data access beyond audit needs. The import only ever populated personnel_id,
# display_name, and entity (identity plumbing, not staffing data), so it added
# complexity without real value. WorkforceStaff.personnel_id remains as an optional
# FK for cross-referencing a compliance record when both exist — not as a data source.
# Maintain the roster via POST/PATCH /workforce/staff.


@router.get("/staff/{staff_id}")
async def get_staff(staff_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    staff = (
        await session.execute(select(WorkforceStaff).where(WorkforceStaff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff not found")
    return _serialize_staff(staff)


@router.post("/staff")
async def create_staff(payload: StaffCreateRequest, session: AsyncSession = Depends(get_db)) -> dict:
    staff = WorkforceStaff(
        display_name=payload.display_name,
        personnel_id=payload.personnel_id,
        entity=payload.entity,
        labor_category=payload.labor_category,
        clearance_level=ClearanceLevel(payload.clearance_level),
        clearance_expiry=payload.clearance_expiry,
        skills=payload.skills,
        certifications=payload.certifications,
        location=payload.location,
        employment_type=payload.employment_type,
        burdened_rate=payload.burdened_rate,
        utilization_pct=payload.utilization_pct,
    )
    session.add(staff)
    await session.flush()
    await log_change(
        session,
        category="workforce",
        action="Staff created",
        subject=str(staff.id),
        detail=f"Staff created: {staff.display_name}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(staff)
    return _serialize_staff(staff)


@router.patch("/staff/{staff_id}")
async def patch_staff(
    staff_id: int,
    payload: StaffPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    staff = (
        await session.execute(select(WorkforceStaff).where(WorkforceStaff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff not found")
    data = payload.model_dump(exclude_unset=True)
    if "clearance_level" in data and data["clearance_level"] is not None:
        data["clearance_level"] = ClearanceLevel(data["clearance_level"])
    for key, value in data.items():
        setattr(staff, key, value)
    staff.updated_at = datetime.utcnow()
    await log_change(
        session,
        category="workforce",
        action="Staff updated",
        subject=str(staff.id),
        detail=f"Staff updated: {staff.display_name}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(staff)
    return _serialize_staff(staff)


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    staff = (
        await session.execute(select(WorkforceStaff).where(WorkforceStaff.id == staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff not found")
    display_name = staff.display_name
    await session.delete(staff)
    await log_change(
        session,
        category="workforce",
        action="Staff deleted",
        subject=str(staff_id),
        detail=f"Staff deleted: {display_name}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "id": staff_id}


# ---------------------------------------------------------------------------
# Pursuits CRUD
# ---------------------------------------------------------------------------


class PursuitCreateRequest(BaseModel):
    title: str
    notice_id: str | None = None
    agency: str | None = None
    naics: str | None = None
    set_aside: str | None = None
    response_due: str | None = None
    required_labor_categories: list[str] | None = None
    required_clearance_level: str | None = None
    key_personnel_slots: int | None = None
    source: str = "manual"


class PursuitPatchRequest(BaseModel):
    title: str | None = None
    notice_id: str | None = None
    agency: str | None = None
    naics: str | None = None
    set_aside: str | None = None
    response_due: str | None = None
    required_labor_categories: list[str] | None = None
    required_clearance_level: str | None = None
    key_personnel_slots: int | None = None
    source: str | None = None


@router.get("/pursuits")
async def list_pursuits(session: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = list(
        (
            await session.execute(select(WorkforcePursuit).order_by(WorkforcePursuit.id.asc()))
        ).scalars()
    )
    return [_serialize_pursuit(row) for row in rows]


@router.get("/pursuits/{pursuit_id}")
async def get_pursuit(pursuit_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    return _serialize_pursuit(pursuit)


@router.post("/pursuits")
async def create_pursuit(
    payload: PursuitCreateRequest, session: AsyncSession = Depends(get_db)
) -> dict:
    clearance = (
        ClearanceLevel(payload.required_clearance_level)
        if payload.required_clearance_level
        else None
    )
    pursuit = WorkforcePursuit(
        title=payload.title,
        notice_id=payload.notice_id,
        agency=payload.agency,
        naics=payload.naics,
        set_aside=payload.set_aside,
        response_due=payload.response_due,
        required_labor_categories=payload.required_labor_categories,
        required_clearance_level=clearance,
        key_personnel_slots=payload.key_personnel_slots,
        source=payload.source or "manual",
    )
    session.add(pursuit)
    await session.flush()
    await log_change(
        session,
        category="workforce",
        action="Pursuit created",
        subject=pursuit.title,
        detail=f"Pursuit created: {pursuit.title}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(pursuit)
    return _serialize_pursuit(pursuit)


@router.patch("/pursuits/{pursuit_id}")
async def patch_pursuit(
    pursuit_id: int,
    payload: PursuitPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    data = payload.model_dump(exclude_unset=True)
    if "required_clearance_level" in data:
        value = data["required_clearance_level"]
        data["required_clearance_level"] = ClearanceLevel(value) if value else None
    for key, value in data.items():
        setattr(pursuit, key, value)
    await log_change(
        session,
        category="workforce",
        action="Pursuit updated",
        subject=pursuit.title,
        detail=f"Pursuit updated: {pursuit.title}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(pursuit)
    return _serialize_pursuit(pursuit)


@router.delete("/pursuits/{pursuit_id}")
async def delete_pursuit(pursuit_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    pursuit = (
        await session.execute(select(WorkforcePursuit).where(WorkforcePursuit.id == pursuit_id))
    ).scalar_one_or_none()
    if pursuit is None:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    title = pursuit.title
    await session.delete(pursuit)
    await log_change(
        session,
        category="workforce",
        action="Pursuit deleted",
        subject=title,
        detail=f"Pursuit deleted: {title}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "id": pursuit_id}


@router.post("/pursuits/{pursuit_id}/gap-analysis")
async def run_gap_analysis(
    pursuit_id: int,
    include_canaide: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
) -> dict:
    try:
        return await analyze_pursuit_gaps(session, pursuit_id, include_canaide=include_canaide)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Assignments CRUD
# ---------------------------------------------------------------------------


class AssignmentCreateRequest(BaseModel):
    staff_id: int
    pursuit_id: int
    role: str | None = None
    commitment_pct: float
    status: str = "proposed"


class AssignmentPatchRequest(BaseModel):
    role: str | None = None
    commitment_pct: float | None = None
    status: str | None = None


@router.get("/assignments")
async def list_assignments(session: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = list(
        (
            await session.execute(
                select(WorkforceAssignment).order_by(WorkforceAssignment.id.asc())
            )
        ).scalars()
    )
    return [_serialize_assignment(row) for row in rows]


@router.get("/assignments/{assignment_id}")
async def get_assignment(assignment_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    assignment = (
        await session.execute(
            select(WorkforceAssignment).where(WorkforceAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return _serialize_assignment(assignment)


@router.post("/assignments")
async def create_assignment(
    payload: AssignmentCreateRequest, session: AsyncSession = Depends(get_db)
) -> dict:
    staff = (
        await session.execute(select(WorkforceStaff).where(WorkforceStaff.id == payload.staff_id))
    ).scalar_one_or_none()
    if staff is None:
        raise HTTPException(status_code=404, detail="Staff not found")
    pursuit = (
        await session.execute(
            select(WorkforcePursuit).where(WorkforcePursuit.id == payload.pursuit_id)
        )
    ).scalar_one_or_none()
    if pursuit is None:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    assignment = WorkforceAssignment(
        staff_id=payload.staff_id,
        pursuit_id=payload.pursuit_id,
        role=payload.role,
        commitment_pct=payload.commitment_pct,
        status=AssignmentStatus(payload.status),
    )
    session.add(assignment)
    await session.flush()
    await log_change(
        session,
        category="workforce",
        action="Assignment created",
        subject=str(assignment.id),
        detail=(
            f"Assignment created: staff={assignment.staff_id} "
            f"pursuit={assignment.pursuit_id}"
        ),
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(assignment)
    return _serialize_assignment(assignment)


@router.patch("/assignments/{assignment_id}")
async def patch_assignment(
    assignment_id: int,
    payload: AssignmentPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    assignment = (
        await session.execute(
            select(WorkforceAssignment).where(WorkforceAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        data["status"] = AssignmentStatus(data["status"])
    for key, value in data.items():
        setattr(assignment, key, value)
    await log_change(
        session,
        category="workforce",
        action="Assignment updated",
        subject=str(assignment.id),
        detail=f"Assignment updated: {assignment.id}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(assignment)
    return _serialize_assignment(assignment)


@router.delete("/assignments/{assignment_id}")
async def delete_assignment(assignment_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    assignment = (
        await session.execute(
            select(WorkforceAssignment).where(WorkforceAssignment.id == assignment_id)
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await session.delete(assignment)
    await log_change(
        session,
        category="workforce",
        action="Assignment deleted",
        subject=str(assignment_id),
        detail=f"Assignment deleted: {assignment_id}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "id": assignment_id}


@router.get("/overcommitment")
async def overcommitment_check(
    include_canaide: bool = Query(default=False),
    session: AsyncSession = Depends(get_db),
) -> dict:
    return await check_overcommitment(session, include_canaide=include_canaide)


# ---------------------------------------------------------------------------
# Gaps CRUD
# ---------------------------------------------------------------------------


class GapCreateRequest(BaseModel):
    pursuit_id: int
    labor_category: str
    clearance_required: str | None = None
    status: str = "open"
    notes: str | None = None


class GapPatchRequest(BaseModel):
    labor_category: str | None = None
    clearance_required: str | None = None
    status: str | None = None
    notes: str | None = None
    resolved_at: str | None = None


@router.get("/gaps")
async def list_gaps(session: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = list(
        (await session.execute(select(WorkforceGap).order_by(WorkforceGap.id.asc()))).scalars()
    )
    return [_serialize_gap(row) for row in rows]


@router.get("/gaps/{gap_id}")
async def get_gap(gap_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    gap = (
        await session.execute(select(WorkforceGap).where(WorkforceGap.id == gap_id))
    ).scalar_one_or_none()
    if gap is None:
        raise HTTPException(status_code=404, detail="Gap not found")
    return _serialize_gap(gap)


@router.post("/gaps")
async def create_gap(payload: GapCreateRequest, session: AsyncSession = Depends(get_db)) -> dict:
    pursuit = (
        await session.execute(
            select(WorkforcePursuit).where(WorkforcePursuit.id == payload.pursuit_id)
        )
    ).scalar_one_or_none()
    if pursuit is None:
        raise HTTPException(status_code=404, detail="Pursuit not found")
    clearance = ClearanceLevel(payload.clearance_required) if payload.clearance_required else None
    gap = WorkforceGap(
        pursuit_id=payload.pursuit_id,
        labor_category=payload.labor_category,
        clearance_required=clearance,
        status=GapStatus(payload.status),
        notes=payload.notes,
    )
    session.add(gap)
    await session.flush()
    await log_change(
        session,
        category="workforce",
        action="Gap created",
        subject=str(gap.id),
        detail=f"Gap created: {gap.labor_category} (pursuit={gap.pursuit_id})",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(gap)
    return _serialize_gap(gap)


@router.patch("/gaps/{gap_id}")
async def patch_gap(
    gap_id: int,
    payload: GapPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    gap = (
        await session.execute(select(WorkforceGap).where(WorkforceGap.id == gap_id))
    ).scalar_one_or_none()
    if gap is None:
        raise HTTPException(status_code=404, detail="Gap not found")
    data = payload.model_dump(exclude_unset=True)
    if "clearance_required" in data:
        value = data["clearance_required"]
        data["clearance_required"] = ClearanceLevel(value) if value else None
    if "status" in data and data["status"] is not None:
        data["status"] = GapStatus(data["status"])
    if "resolved_at" in data:
        value = data["resolved_at"]
        data["resolved_at"] = datetime.fromisoformat(value) if value else None
    for key, value in data.items():
        setattr(gap, key, value)
    await log_change(
        session,
        category="workforce",
        action="Gap updated",
        subject=str(gap.id),
        detail=f"Gap updated: {gap.labor_category}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(gap)
    return _serialize_gap(gap)


@router.delete("/gaps/{gap_id}")
async def delete_gap(gap_id: int, session: AsyncSession = Depends(get_db)) -> dict:
    gap = (
        await session.execute(select(WorkforceGap).where(WorkforceGap.id == gap_id))
    ).scalar_one_or_none()
    if gap is None:
        raise HTTPException(status_code=404, detail="Gap not found")
    labor_category = gap.labor_category
    await session.delete(gap)
    await log_change(
        session,
        category="workforce",
        action="Gap deleted",
        subject=str(gap_id),
        detail=f"Gap deleted: {labor_category}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "id": gap_id}

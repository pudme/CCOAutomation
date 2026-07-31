from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.compliance import Control, CorrectiveAction, Finding, FindingStatus, finding_control_association
from services.change_log import log_change

router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("")
async def list_findings(
    framework: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    owner: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Finding).options(
        selectinload(Finding.controls), selectinload(Finding.corrective_actions)
    )
    if framework:
        stmt = stmt.where(Finding.framework_id == int(framework))
    if severity:
        stmt = stmt.where(Finding.severity == severity)
    if status:
        stmt = stmt.where(Finding.status == status)
    if owner:
        stmt = stmt.where(Finding.owner == owner)
    result = await session.execute(stmt.order_by(Finding.finding_id.asc()))
    findings = list(result.scalars())
    return [_serialize_finding(finding) for finding in findings]


@router.get("/{finding_id}")
async def get_finding(finding_id: str, session: AsyncSession = Depends(get_db)) -> dict:
    result = await session.execute(
        select(Finding)
        .options(selectinload(Finding.controls), selectinload(Finding.corrective_actions))
        .where(Finding.finding_id == finding_id)
    )
    finding = result.scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    payload = _serialize_finding(finding)
    payload["timeline"] = [
        {"status": finding.status.value, "timestamp": finding.discovered_date or "unknown"},
        {"status": "last_updated", "timestamp": finding.closed_date or "open"},
    ]
    return payload


class FindingPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    owner: str | None = None


@router.patch("/{finding_id}")
async def patch_finding(
    finding_id: str,
    payload: FindingPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(select(Finding).where(Finding.finding_id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    previous_status = finding.status.value
    if payload.status:
        finding.status = FindingStatus(payload.status)
    if payload.notes:
        finding.description = payload.notes
    if payload.owner:
        finding.owner = payload.owner
    if payload.status and payload.status != previous_status:
        await log_change(
            session,
            category="finding",
            action="Finding status changed",
            subject=finding.finding_id,
            detail=f"Finding {finding.finding_id} status changed to {finding.status.value}",
        )
    elif payload.notes or payload.owner:
        await log_change(
            session,
            category="finding",
            action="Finding updated",
            subject=finding.finding_id,
            detail=f"Finding {finding.finding_id} notes/owner updated",
            triggered_by="api",
        )
    await session.commit()
    await session.refresh(finding)
    return {"status": "updated", "finding_id": finding.finding_id}


class CorrectiveActionCreateRequest(BaseModel):
    description: str
    owner: str | None = None
    due_date: str | None = None
    status: str | None = None
    notes: str | None = None


@router.post("/{finding_id}/corrective-actions")
async def add_corrective_action(
    finding_id: str,
    payload: CorrectiveActionCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    result = await session.execute(select(Finding).where(Finding.finding_id == finding_id))
    finding = result.scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    action = CorrectiveAction(
        finding_id=finding.id,
        description=payload.description,
        owner=payload.owner,
        due_date=payload.due_date,
        status=FindingStatus(payload.status or "open"),
        notes=payload.notes,
    )
    session.add(action)
    await session.flush()
    await log_change(
        session,
        category="finding",
        action="Corrective action created",
        subject=finding.finding_id,
        detail=f"Corrective action created for {finding.finding_id}: {action.description}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(action)
    return {"id": action.id, "finding_id": finding.finding_id}


class CorrectiveActionPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    owner: str | None = None
    due_date: str | None = None


@router.patch("/{finding_id}/corrective-actions/{ca_id}")
async def patch_corrective_action(
    finding_id: str,
    ca_id: int,
    payload: CorrectiveActionPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    finding = (
        await session.execute(select(Finding).where(Finding.finding_id == finding_id))
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    action = (
        await session.execute(
            select(CorrectiveAction).where(
                CorrectiveAction.id == ca_id, CorrectiveAction.finding_id == finding.id
            )
        )
    ).scalar_one_or_none()
    if action is None:
        raise HTTPException(status_code=404, detail="Corrective action not found")
    if payload.status:
        action.status = FindingStatus(payload.status)
    if payload.notes is not None:
        action.notes = payload.notes
    if payload.owner is not None:
        action.owner = payload.owner
    if payload.due_date is not None:
        action.due_date = payload.due_date
    await log_change(
        session,
        category="finding",
        action="Corrective action updated",
        subject=finding.finding_id,
        detail=f"Corrective action {action.id} updated for {finding.finding_id}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "updated", "id": action.id}


def _serialize_finding(finding: Finding) -> dict:
    return {
        "finding_id": finding.finding_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value,
        "status": finding.status.value,
        "framework_id": finding.framework_id,
        "owner": finding.owner,
        "linked_controls": [control.control_id for control in finding.controls],
        "corrective_actions": [
            {
                "id": action.id,
                "description": action.description,
                "owner": action.owner,
                "due_date": action.due_date,
                "status": action.status.value,
                "notes": action.notes,
            }
            for action in finding.corrective_actions
        ],
    }


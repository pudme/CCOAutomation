from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models.compliance import Control, ControlStatus, EvidenceControlLink
from services.change_log import log_change

router = APIRouter(prefix="/controls", tags=["controls"])


@router.get("")
async def list_controls(session: AsyncSession = Depends(get_db)) -> list[dict]:
    controls = list((await session.execute(select(Control).order_by(Control.control_id.asc()))).scalars())
    return [{"id": c.id, "control_id": c.control_id, "title": c.title, "status": c.status.value} for c in controls]


@router.get("/{control_id}")
async def get_control(control_id: str, session: AsyncSession = Depends(get_db)) -> dict:
    control = (
        await session.execute(
            select(Control)
            .options(
                selectinload(Control.evidence_links).selectinload(EvidenceControlLink.evidence),
                selectinload(Control.evidence_requirements),
                selectinload(Control.findings),
                selectinload(Control.mapped_to),
            )
            .where(Control.control_id == control_id)
        )
    ).scalar_one_or_none()
    if control is None:
        raise HTTPException(status_code=404, detail="Control not found")
    return {
        "id": control.id,
        "control_id": control.control_id,
        "title": control.title,
        "framework_id": control.framework_id,
        "domain": control.domain,
        "description": control.description,
        "implementation_guidance": control.implementation_guidance,
        "status": control.status.value,
        "status_notes": control.status_notes,
        "owner": control.owner_default,
        "last_reviewed": control.last_reviewed,
        "evidence_items": [
            {
                "id": e.id,
                "filename": e.filename,
                "type": e.evidence_type.value,
                "date": e.collected_date,
                "entity": e.entity,
                "status": e.status.value,
            }
            for e in control.evidence_items
        ],
        "evidence_requirements": [
            {"id": r.id, "type": r.evidence_type.value, "description": r.description, "required": r.required}
            for r in control.evidence_requirements
        ],
        "cross_mapped_controls": [c.control_id for c in control.mapped_to],
        "linked_findings": [f.finding_id for f in control.findings],
    }


class ControlPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None


@router.patch("/{control_id}")
async def patch_control(
    control_id: str,
    payload: ControlPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    control = (
        await session.execute(select(Control).where(Control.control_id == control_id))
    ).scalar_one_or_none()
    if control is None:
        raise HTTPException(status_code=404, detail="Control not found")
    if payload.status:
        control.status = ControlStatus(payload.status)
    if payload.notes is not None:
        control.status_notes = payload.notes
    await log_change(
        session,
        category="control",
        action="Control updated",
        subject=control.control_id,
        detail=f"Control updated: {control.control_id} status={control.status.value}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "updated", "control_id": control.control_id}


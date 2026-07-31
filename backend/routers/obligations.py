from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.compliance import Obligation, ObligationStatus
from services.change_log import log_change

router = APIRouter(prefix="/obligations", tags=["obligations"])


@router.get("")
async def list_obligations(
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
) -> list[dict]:
    stmt = select(Obligation).order_by(Obligation.obligation_id.asc())
    result = await session.execute(stmt)
    obligations = list(result.scalars())
    payload = [_serialize_obligation(o) for o in obligations]
    if status:
        payload = [row for row in payload if row["status"] == status]
    return [row for row in payload if not row["deleted"]]


class ObligationCreateRequest(BaseModel):
    source: str
    description: str
    owner: str | None = None
    due_date: str | None = None
    cadence: str | None = None
    status: str | None = None


@router.post("")
async def create_obligation(
    payload: ObligationCreateRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    latest = (
        await session.execute(select(Obligation).order_by(Obligation.id.desc()))
    ).scalars().first()
    next_num = (latest.id + 1) if latest else 1
    obligation = Obligation(
        obligation_id=f"OBL-{next_num:03d}",
        source=payload.source,
        description=payload.description,
        owner=payload.owner,
        due_date=payload.due_date,
        cadence=payload.cadence,
        status=ObligationStatus(payload.status or "current"),
        notes="",
    )
    session.add(obligation)
    await session.flush()
    await log_change(
        session,
        category="obligation",
        action="Obligation created",
        subject=obligation.obligation_id,
        detail=f"Obligation created: {obligation.obligation_id}",
        triggered_by="api",
    )
    await session.commit()
    await session.refresh(obligation)
    return _serialize_obligation(obligation)


class ObligationPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    due_date: str | None = None
    last_satisfied: str | None = None
    owner: str | None = None


@router.patch("/{obligation_id}")
async def patch_obligation(
    obligation_id: str,
    payload: ObligationPatchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict:
    obligation = (
        await session.execute(select(Obligation).where(Obligation.obligation_id == obligation_id))
    ).scalar_one_or_none()
    if obligation is None:
        raise HTTPException(status_code=404, detail="Obligation not found")
    if payload.status:
        obligation.status = ObligationStatus(payload.status)
    if payload.notes is not None:
        obligation.notes = payload.notes
    if payload.due_date is not None:
        obligation.due_date = payload.due_date
    if payload.last_satisfied is not None:
        obligation.last_satisfied = payload.last_satisfied
    if payload.owner is not None:
        obligation.owner = payload.owner
    await log_change(
        session,
        category="obligation",
        action="Obligation updated",
        subject=obligation.obligation_id,
        detail=f"Obligation updated: {obligation.obligation_id}",
        triggered_by="api",
    )
    await session.commit()
    return _serialize_obligation(obligation)


@router.delete("/{obligation_id}")
async def soft_delete_obligation(
    obligation_id: str,
    session: AsyncSession = Depends(get_db),
) -> dict:
    obligation = (
        await session.execute(select(Obligation).where(Obligation.obligation_id == obligation_id))
    ).scalar_one_or_none()
    if obligation is None:
        raise HTTPException(status_code=404, detail="Obligation not found")
    marker = "[SOFT_DELETED]"
    if marker not in (obligation.notes or ""):
        obligation.notes = f"{marker} {(obligation.notes or '').strip()}".strip()
    await log_change(
        session,
        category="obligation",
        action="Obligation deleted",
        subject=obligation.obligation_id,
        detail=f"Obligation deleted: {obligation.obligation_id}",
        triggered_by="api",
    )
    await session.commit()
    return {"status": "deleted", "obligation_id": obligation.obligation_id}


def _serialize_obligation(obligation: Obligation) -> dict:
    notes = obligation.notes or ""
    return {
        "obligation_id": obligation.obligation_id,
        "source": obligation.source,
        "description": obligation.description,
        "owner": obligation.owner,
        "due_date": obligation.due_date,
        "cadence": obligation.cadence,
        "status": obligation.status.value,
        "last_satisfied": obligation.last_satisfied,
        "notes": obligation.notes,
        "deleted": "[SOFT_DELETED]" in notes,
    }

